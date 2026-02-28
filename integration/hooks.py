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
import json
import sys
from pathlib import Path

from governance.service import GovernanceService

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


class GovernanceHook:
    """Hook handler for Claude Code integration.

    Wraps GovernanceService with event-sourced state persistence
    so that state survives across individual hook invocations.
    """

    def __init__(self, org_spec_path: str | Path, state_path: str | Path = ".aorta/state.json"):
        self._org_spec_path = Path(org_spec_path)
        self._state_path = Path(state_path)
        self._service = GovernanceService(self._org_spec_path)
        self._events: list[dict] = []
        self._replay_state()

    def _replay_state(self):
        """Replay events from state file to reconstruct service state."""
        if not self._state_path.exists():
            return
        state = json.loads(self._state_path.read_text())
        self._events = state.get("events", [])
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

    def _save_state(self):
        """Persist events to state file."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({"events": self._events}, indent=2))

    def register_agent(self, agent: str, role: str, scope: str = ""):
        """Register an agent with a role and scope, persisting the event."""
        self._service.register_agent(agent, role, scope)
        self._events.append({
            "type": "register", "agent": agent, "role": role, "scope": scope,
        })
        self._save_state()

    def pre_tool_use(self, context: dict, agent: str | None = None) -> dict:
        """Handle PreToolUse hook.

        Args:
            context: Tool call context from Claude Code
                     {"tool_name": "Write", "tool_input": {"file_path": "..."}}
            agent: Agent ID (override context if provided)

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
            return {"decision": "approve"}

        params = {}
        if "file_path" in tool_input:
            params["path"] = tool_input["file_path"]
        elif "path" in tool_input:
            params["path"] = tool_input["path"]

        result = self._service.check_permission(agent_id, role, action, params)

        if result.permitted:
            return {"decision": "approve"}
        return {
            "decision": "block",
            "reason": result.reason,
        }

    def post_tool_use(self, context: dict, agent: str | None = None) -> dict:
        """Handle PostToolUse hook.

        Returns status and any norm changes from the action notification.
        """
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

    def _get_agent_role(self, agent: str) -> str | None:
        """Look up the role for a registered agent."""
        results = list(self._service._engine._prolog.query(
            f"rea('{agent}', Role), term_to_atom(Role, RoleS)"
        ))
        if results:
            return str(results[0]["RoleS"])
        return None


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
    parser.add_argument("--state", default=".aorta/state.json", help="State file path")
    parser.add_argument("--agent", help="Agent ID")
    parser.add_argument("--role", help="Role (for register)")
    parser.add_argument("--scope", default="", help="Scope (for register)")

    args = parser.parse_args()
    hook = GovernanceHook(args.org_spec, args.state)

    if args.command == "register":
        if not args.agent or not args.role:
            parser.error("register requires --agent and --role")
        hook.register_agent(args.agent, args.role, args.scope)
        _respond({"ok": True})

    elif args.command == "pre-tool-use":
        context = json.loads(sys.stdin.read())
        result = hook.pre_tool_use(context, agent=args.agent)
        _respond(result)

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


if __name__ == "__main__":
    main()
