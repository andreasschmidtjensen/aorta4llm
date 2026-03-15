"""aorta hook — Claude Code hook entry point.

Thin wrapper so hooks can use `aorta hook pre-tool-use ...` instead of
`uv run python -m integration.hooks pre-tool-use ...`. This makes hook
commands portable across projects without needing PYTHONPATH hacks.
"""

import json
import os
import re
import sys


def add_parser(subparsers):
    p = subparsers.add_parser("hook", help="Claude Code hook handler")
    p.add_argument(
        "hook_command",
        choices=["pre-tool-use", "post-tool-use", "register", "prompt", "session-start"],
    )
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--state", default=None, help="State file path")
    p.add_argument("--agent", help="Agent ID")
    p.add_argument("--role", help="Role (for register)")
    p.add_argument("--scope", default="", help="Scope (for register)")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("--cwd", default=None, help="Project root for path normalization")
    p.set_defaults(func=run)


def run(args):
    from aorta4llm.integration.hooks import GovernanceHook

    # Support AORTA_AGENT env var as fallback for --agent.
    agent = args.agent or os.environ.get("AORTA_AGENT")

    hook = GovernanceHook(args.org_spec, args.state, events_path=args.events_path)

    if args.hook_command == "register":
        if not agent or not args.role:
            print("register requires --agent and --role", file=sys.stderr)
            raise SystemExit(1)
        hook.register_agent(agent, args.role, args.scope)
        print(json.dumps({"ok": True}), flush=True)

    elif args.hook_command == "pre-tool-use":
        context = json.loads(sys.stdin.read())
        result = hook.pre_tool_use(context, agent=agent, project_cwd=args.cwd)
        reset_notice = result.pop("_reset_notice", None)
        _respond_hook(result)
        if reset_notice:
            print(reset_notice, file=sys.stderr, flush=True)

    elif args.hook_command == "post-tool-use":
        context = json.loads(sys.stdin.read())
        result = hook.post_tool_use(context, agent=agent)
        warning = result.pop("_sensitive_warning", None)
        achievement = result.pop("_achievement_notice", None)
        piped = result.pop("_piped_notice", None)
        notices = []
        if warning:
            print(warning, file=sys.stderr, flush=True)
            sys.exit(2)
        if achievement:
            notices.append(achievement)
        if piped:
            notices.append(piped)
        if notices:
            print("\n".join(notices), file=sys.stderr, flush=True)
            sys.exit(2)
        print(json.dumps(result), flush=True)

    elif args.hook_command == "prompt":
        if not agent:
            print("prompt requires --agent", file=sys.stderr)
            raise SystemExit(1)
        text = hook.get_system_prompt_injection(agent)
        if text:
            print(text)

    elif args.hook_command == "session-start":
        import io
        from aorta4llm.cli.cmd_context import run as run_context

        # Capture context output
        old_stdout = sys.stdout
        sys.stdout = buf = io.StringIO()

        class _Args:
            org_spec = args.org_spec

        run_context(_Args())
        context_text = buf.getvalue()
        sys.stdout = old_stdout

        # Append obligation injection if agent is registered
        if agent:
            obligations = hook.get_system_prompt_injection(agent)
            if obligations:
                context_text += "\n" + obligations

        if context_text.strip():
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context_text.strip(),
                }
            }), flush=True)


def _respond_hook(result: dict):
    """Respond in Claude Code hook format."""
    if result.get("decision") == "block":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result.get("reason", "blocked by governance"),
            }
        }), flush=True)
