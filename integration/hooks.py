"""Claude Code hook integration for aorta4llm governance.

Provides PreToolUse and PostToolUse hook handlers that enforce
organizational norms on Claude Code tool calls.

State is persisted via event sourcing: a JSON file stores the sequence
of registrations and state changes. Each hook invocation replays events
to reconstruct the governance service state.

Usage (CLI):
    # Register an agent
    python -m integration.hooks register \\
        --org-spec org-specs/three_role_workflow.yaml \\
        --agent impl-1 --role implementer --scope src/auth/

    # PreToolUse hook (reads tool context from stdin)
    echo '{"tool_name":"Write","tool_input":{"file_path":"src/api/x.py"}}' | \\
        python -m integration.hooks pre-tool-use \\
        --org-spec org-specs/three_role_workflow.yaml --agent impl-1

Claude Code hook configuration (.claude/settings.local.json):
    {
      "hooks": {
        "PreToolUse": [{
          "matcher": "Write|Edit|Bash",
          "command": "uv run python -m integration.hooks pre-tool-use --org-spec org-specs/three_role_workflow.yaml --agent $AGENT_NAME"
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
    "Agent": "spawn_agent",
    "Glob": "read_file",
    "Grep": "read_file",
    "NotebookEdit": "write_file",
}


# Patterns that indicate a governance command (agents must never run these).
_GOVERNANCE_CMD_PATTERNS = re.compile(
    r"(?:^|\s|&&|\|\||;)"  # start of string or command separator
    r"(?:aorta\s|python\s+-m\s+(?:cli|integration\.hooks)\s)",
)


def _is_governance_command(cmd: str) -> bool:
    """Check if a bash command invokes aorta governance tools."""
    return bool(_GOVERNANCE_CMD_PATTERNS.search(cmd))


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
        self._reset_on_file_change: set[str] = {
            t["marks"] for t in self._triggers if t.get("reset_on_file_change")
        }
        self._org_spec_name = self._org_spec_path.stem
        self._replay_state()

    def _load_spec_extras(self, org_spec_path: Path) -> tuple[list[dict], bool, frozenset[str], int]:
        """Load achievement_triggers, bash_analysis flag, safe_commands, and soft_block_window."""
        try:
            with open(org_spec_path) as f:
                spec = yaml.safe_load(f)
            safe_cmds = frozenset(spec.get("safe_commands", []))
            window = int(spec.get("soft_block_window", 60))
            return (
                spec.get("achievement_triggers", []),
                bool(spec.get("bash_analysis", False)),
                safe_cmds,
                window,
            )
        except Exception:
            return [], False, frozenset(), 60

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
                     project_cwd: str | None = None) -> dict:
        """Handle PreToolUse hook.

        Args:
            context: Tool call context from Claude Code
                     {"tool_name": "Write", "tool_input": {"file_path": "..."}}
            agent: Agent ID (override context if provided)
            project_cwd: Explicit project root for path normalization.

        Returns:
            {"decision": "approve"} or {"decision": "block", "reason": "..."}
        """
        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})

        action = TOOL_ACTION_MAP.get(tool_name)
        if not action:
            return {"decision": "approve"}

        agent_id = agent or context.get("agent_name", "default-agent")
        role = self._get_agent_role(agent_id)
        if not role:
            reason = f"agent '{agent_id}' is not registered — action denied (fail-closed)"
            self._log({
                "type": "check", "agent": agent_id, "role": "unknown",
                "action": action, "path": "",
                "decision": "block", "reason": reason,
            })
            return {"decision": "block", "reason": reason}

        params = {}
        if tool_name == "Bash":
            params["command"] = tool_input.get("command", "")
        else:
            raw_path = tool_input.get("file_path") or tool_input.get("path") or ""
            if raw_path and os.path.isabs(raw_path):
                # Claude Code sends absolute paths; make relative to project root.
                # Try explicit cwd, context cwd, auto-detected root, then process cwd.
                detected_root = _detect_project_root(self._org_spec_path)
                for candidate in [project_cwd, context.get("cwd", ""), detected_root, os.getcwd()]:
                    if not candidate:
                        continue
                    prefix = str(candidate).rstrip("/") + "/"
                    if raw_path.startswith(prefix):
                        raw_path = raw_path[len(prefix):]
                        break
            if raw_path:
                params["path"] = raw_path

        # Hard-block writes to governance infrastructure, regardless of org spec.
        if action == "write_file" and params.get("path"):
            for protected in PROTECTED_PATHS:
                if params["path"].startswith(protected) or params["path"] == protected.rstrip("/"):
                    reason = f"write to '{params['path']}' denied: governance infrastructure is protected"
                    self._log({
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
                self._log({
                    "type": "check", "agent": agent_id, "role": role,
                    "action": action, "path": "",
                    "decision": "block", "reason": reason, "severity": "hard",
                })
                return {"decision": "block", "reason": reason}

        # Check for allow-once exceptions before norm evaluation.
        path = params.get("path", "")
        if path and self._check_exception(path, agent_id):
            self._log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": path,
                "decision": "approve", "reason": "allow-once exception",
            })
            return {"decision": "approve"}

        result = self._service.check_permission(agent_id, role, action, params)

        if not result.permitted:
            if result.severity == "soft":
                soft_result = self._handle_soft_block(result.reason, params)
                self._log({
                    "type": "check", "agent": agent_id, "role": role,
                    "action": action, "path": params.get("path", ""),
                    "decision": soft_result["decision"],
                    "reason": result.reason, "severity": "soft",
                })
                return soft_result
            reason = result.reason
            self._log({
                "type": "check", "agent": agent_id, "role": role,
                "action": action, "path": params.get("path", ""),
                "decision": "block", "reason": reason, "severity": "hard",
            })
            if path:
                spec_rel = str(self._org_spec_path)
                reason += f"\n  To grant a one-time exception: aorta allow-once --org-spec {spec_rel} --path {path}"
            return {"decision": "block", "reason": reason}

        self._log({
            "type": "check", "agent": agent_id, "role": role,
            "action": action, "path": params.get("path", ""),
            "decision": "approve",
        })

        # Phase 2: LLM-based Bash command analysis.
        if tool_name == "Bash" and self._bash_analysis and params.get("command"):
            from governance.bash_analyzer import analyze_bash_command

            analysis = analyze_bash_command(params["command"], extra_safe=self._safe_commands)
            for write_path in analysis.writes:
                path_result = self._service.check_permission(
                    agent_id, role, "write_file", {"path": write_path},
                )
                if not path_result.permitted:
                    self._log(
                        {"type": "bash_analysis", "agent": agent_id,
                         "command": params["command"], "writes": analysis.writes,
                         "decision": "block", "reason": path_result.reason},
                    )
                    return {
                        "decision": "block",
                        "reason": f"Bash command writes to '{write_path}': {path_result.reason}",
                    }
            if analysis.writes:
                self._log(
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
                    self._log({"type": "achievement_reset", "agent": agent_id,
                               "mark": mark, "reason": "file changed"})

        return {"decision": "approve"}

    def post_tool_use(self, context: dict, agent: str | None = None) -> dict:
        """Handle PostToolUse hook.

        Checks achievement_triggers from the org spec. When a trigger matches
        the tool name, command pattern, and exit code, marks the corresponding
        achievement and notifies the governance engine.
        """
        if not self._triggers:
            return {"status": "ok"}

        tool_name = context.get("tool_name", "")
        tool_input = context.get("tool_input", {})
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
        return {
            "decision": "block",
            "reason": (
                f"SOFT BLOCK — user confirmation required.\n"
                f"Reason: {reason}\n"
                f"Action: Ask the user whether they want to proceed. "
                f"If the user confirms, retry the EXACT same command. "
                f"The retry will be approved automatically within {self._soft_block_window}s."
            ),
        }

    @staticmethod
    def _soft_block_key(params: dict) -> str:
        """Generate a cache key for soft block deduplication."""
        raw = params.get("command", "") or params.get("path", "")
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

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
