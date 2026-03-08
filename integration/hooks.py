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

from governance.service import GovernanceService
from integration.events import log_event

# Paths that are always protected regardless of org spec configuration.
# Prevents agents from modifying their own governance infrastructure.
PROTECTED_PATHS = (".aorta/", ".claude/")

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
    "status", "permissions", "explain", "validate", "dry-run", "doctor", "watch",
})


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
        self._bash_analysis = extras[1]
        self._safe_commands = extras[2]
        self._events: list[dict] = []
        self._replaying = False
        self._soft_block_cache: dict[str, float] = {}  # command_hash -> timestamp
        self._exceptions: list[dict] = []
        self._soft_block_window = extras[3]
        self._sensitive_paths = extras[4]
        self._reset_on_file_change: set[str] = {
            t["marks"] for t in self._triggers if t.get("reset_on_file_change")
        }
        self._org_spec_name = self._org_spec_path.stem
        self._replay_state()

    def _load_spec_extras(self, org_spec_path: Path) -> tuple[list[dict], bool, frozenset[str], int, frozenset[str]]:
        """Load achievement_triggers, bash_analysis flag, safe_commands, soft_block_window, and sensitive paths."""
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
            return (
                spec.get("achievement_triggers", []),
                bool(spec.get("bash_analysis", False)),
                safe_cmds,
                window,
                sensitive,
            )
        except Exception:
            return [], False, frozenset(), 60, frozenset()

    def _replay_state(self):
        """Replay events from state file to reconstruct service state."""
        if not self._state_path.exists():
            return
        self._replaying = True
        state = json.loads(self._state_path.read_text())
        self._events = state.get("events", [])
        self._soft_block_cache = state.get("soft_blocks", {})
        self._exceptions = state.get("exceptions", [])
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
        self._replaying = False

    def _save_state(self):
        """Persist events, soft block timestamps, and exceptions to state file."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        state: dict = {
            "events": self._events,
            "soft_blocks": self._soft_block_cache,
        }
        if self._exceptions:
            state["exceptions"] = self._exceptions
        self._state_path.write_text(json.dumps(state, indent=2))

    def clear_transient_state(self):
        """Clear exceptions and soft block cache. Called during reinit."""
        self._soft_block_cache.clear()
        self._exceptions.clear()
        self._save_state()

    def register_agent(self, agent: str, role: str, scope: str = "",
                       reinit: bool = False):
        """Register an agent with a role and scope, persisting the event."""
        self._service.register_agent(agent, role, scope)
        event = {"type": "register", "agent": agent, "role": role, "scope": scope}
        if reinit:
            event["reinit"] = True
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
        if tool_name == "Bash":
            raw_command = tool_input.get("command", "")
            # Normalize git commands for pattern matching (strips -C <path> etc.)
            params["command"] = _normalize_git_cmd(raw_command)
        else:
            raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
            raw_path = _make_relative(
                raw_path, self._org_spec_path,
                project_cwd=project_cwd,
                context_cwd=context.get("cwd", ""),
            )
            if raw_path:
                params["path"] = raw_path

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
        if path and self._check_exception(path, agent_id):
            log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": path,
                "decision": "approve", "reason": "allow-once exception",
            })
            return {"decision": "approve"}

        result = self._service.check_permission(agent_id, role, action, params)

        if not result.permitted:
            if result.severity == "soft":
                soft_result = self._handle_soft_block(result.reason, params)
                log({
                    "type": "check", "agent": agent_id, "role": role,
                    "action": action, "path": params.get("path", ""),
                    "decision": soft_result["decision"],
                    "reason": _truncate_reason(result.reason), "severity": "soft",
                })
                return soft_result
            short_reason = _shorten_block_reason(result.reason)
            display = _display_action(action, path)
            user_reason = f"{display} blocked: {short_reason}"
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
            return {"decision": "block", "reason": user_reason}

        log({
            "type": "check", "agent": agent_id, "role": role,
            "action": action, "path": params.get("path", ""),
            "decision": "approve",
        })

        # Phase 2: LLM-based Bash command analysis.
        # Use raw_command (not normalized) so path extraction sees actual paths.
        if tool_name == "Bash" and self._bash_analysis and raw_command:
            from governance.bash_analyzer import analyze_bash_command

            analysis = analyze_bash_command(raw_command, extra_safe=self._safe_commands)
            for write_path in analysis.writes:
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
        if action == "write_file" and self._reset_on_file_change:
            for mark in list(self._reset_on_file_change):
                if self._service.clear_achievement(mark):
                    # Remove from persisted events too
                    self._events = [
                        e for e in self._events
                        if not (e.get("type") == "achieved" and mark in e.get("objectives", []))
                    ]
                    self._save_state()
                    log({"type": "achievement_reset", "agent": agent_id,
                         "mark": mark, "reason": "file changed"})

        return {"decision": "approve"}

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

        if not self._triggers:
            return {"status": "ok"}

        tool_response = context.get("tool_response", {})
        exit_code = tool_response.get("exit_code", 0)

        agent_id = agent or context.get("agent_name", "default-agent")
        role = self._get_agent_role(agent_id)
        if not role:
            return {"status": "ok"}

        achieved = []
        for trigger in self._triggers:
            if trigger.get("tool") != tool_name:
                continue
            required_exit = trigger.get("exit_code")
            if required_exit is not None and exit_code != required_exit:
                continue
            pattern = trigger.get("command_pattern", "")
            if pattern:
                cmd = tool_input.get("command", "")
                # Normalize git flags so 'git -C /path ...' matches patterns.
                cmd = _normalize_git_cmd(cmd)
                if not re.search(pattern, cmd):
                    continue
            achieved.append(trigger["marks"])

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

        return {"status": "ok"}

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

    def _check_exception(self, path: str, agent: str) -> bool:
        """Check if an allow-once exception exists for this path+agent.

        If found, decrements uses and removes when exhausted. Returns True if allowed.
        """
        for exc in self._exceptions:
            if exc["path"] != path:
                # Check if path starts with exception path (prefix match)
                if not path.startswith(exc["path"]):
                    continue
            if exc["agent"] != "*" and exc["agent"] != agent:
                continue
            exc["uses"] -= 1
            if exc["uses"] <= 0:
                self._exceptions.remove(exc)
            self._save_state()
            return True
        return False

    def _handle_soft_block(self, reason: str, params: dict) -> dict:
        """Handle a soft (confirmation-required) block.

        On first encounter: block with a message inviting retry.
        On retry within the time window: approve (user confirmed).
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
        return {
            "decision": "block",
            "reason": (
                f"SOFT BLOCK: {short_reason}\n"
                f"Ask the user to confirm, then retry the exact same command."
            ),
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
        choices=["pre-tool-use", "post-tool-use", "register", "prompt"],
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
        _respond_hook(result)

    elif args.command == "post-tool-use":
        context = json.loads(sys.stdin.read())
        result = hook.post_tool_use(context, agent=args.agent)
        warning = result.pop("_sensitive_warning", None)
        if warning:
            print(warning, file=sys.stderr, flush=True)
            sys.exit(2)
        _respond(result)

    elif args.command == "prompt":
        if not args.agent:
            parser.error("prompt requires --agent")
        text = hook.get_system_prompt_injection(args.agent)
        if text:
            print(text)


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
