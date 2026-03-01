"""Agent runner — spawns Claude Code CLI with governance enforcement.

Each agent runs as a Claude Code subprocess with all tools available.
Governance is enforced via CLI hooks (PreToolUse) configured in
.claude/settings.local.json. The hook command invokes integration.hooks
which checks permissions against the GovernanceService.

This approach uses the stable CLI interface rather than the Python SDK's
streaming mode, which has compatibility issues with newer message types
(e.g. rate_limit_event).
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

from integration.events import log_event

# Default events path
_EVENTS_PATH = Path(".aorta/events.jsonl")


def _find_claude_cli() -> str:
    """Find the claude CLI binary."""
    cli = shutil.which("claude")
    if cli:
        return cli
    raise FileNotFoundError(
        "Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
    )


def _write_hook_settings(
    settings_dir: Path,
    org_spec_path: str,
    agent_id: str,
    state_path: str,
    events_path: str,
) -> None:
    """Write .claude/settings.local.json with governance hook config."""
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / "settings.local.json"

    settings = {
        "permissions": {
            "allow": [
                "Bash(*)", "Read(*)", "Write(*)", "Edit(*)",
                "Glob(*)", "Grep(*)", "WebSearch(*)", "WebFetch(*)",
            ],
            "deny": [],
        },
        "hooks": {
            "PreToolUse": [{
                "matcher": "Write|Edit|NotebookEdit",
                "hooks": [{
                    "type": "command",
                    "command": (
                        f"uv run python -m integration.hooks pre-tool-use"
                        f" --org-spec {org_spec_path}"
                        f" --agent {agent_id}"
                        f" --state {state_path}"
                        f" --events-path {events_path}"
                    ),
                    "timeout": 10000,
                }],
            }],
        },
    }

    settings_file.write_text(json.dumps(settings, indent=2))


async def run_agent(
    agent_id: str,
    role: str,
    org_spec_path: str,
    prompt: str,
    system_prompt: str,
    allowed_tools: list[str] | None = None,
    model: str = "sonnet",
    cwd: str | Path | None = None,
    events_path: Path = _EVENTS_PATH,
    state_path: Path | None = None,
    max_turns: int = 10,
) -> str:
    """Run a governed Claude Code agent as a subprocess.

    Governance is enforced through CLI hooks — the PreToolUse hook calls
    integration.hooks which checks permissions against the GovernanceService.
    The hook reads state from a shared state file, so agent registrations
    and achievements set by the orchestrator are visible to the hook.

    Args:
        agent_id: Unique agent identifier.
        role: Organizational role.
        org_spec_path: Absolute path to the org spec YAML.
        prompt: Task prompt for the agent.
        system_prompt: System prompt with organizational context.
        allowed_tools: CLI tool whitelist. None = all tools.
        model: Model to use.
        cwd: Working directory for the agent.
        events_path: Path to events JSONL for logging.
        state_path: Path to shared governance state file.
        max_turns: Maximum agentic turns.

    Returns:
        Collected text output from the agent.
    """
    cli = _find_claude_cli()
    work_dir = str(cwd) if cwd else "."
    abs_events = str(events_path.resolve())
    abs_state = str(state_path.resolve()) if state_path else str(
        (events_path.parent / "state.json").resolve()
    )
    abs_org_spec = str(Path(org_spec_path).resolve())

    # Write per-agent .claude/settings.local.json with governance hooks
    agent_settings_dir = Path(work_dir) / ".claude"
    _write_hook_settings(agent_settings_dir, abs_org_spec, agent_id, abs_state, abs_events)

    # Build CLI command
    cmd = [
        cli,
        "--print",
        "--output-format", "text",
        "--model", model,
        "--system-prompt", system_prompt,
        "--max-turns", str(max_turns),
        "--permission-mode", "bypassPermissions",
        "--disallowedTools", "Agent,EnterPlanMode",
        "--verbose",
    ]

    if allowed_tools:
        cmd.extend(["--allowedTools", ",".join(allowed_tools)])

    # Prompt goes last after --
    cmd.extend(["--", prompt])

    # Environment: strip CLAUDECODE to avoid nested session detection
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    env["AORTA_AGENT"] = agent_id
    env["AORTA_ROLE"] = role

    print(f"  [{agent_id}] Starting claude CLI (model={model}, max_turns={max_turns})", flush=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir,
        env=env,
    )

    stdout, stderr = await proc.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    err_output = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        print(f"  [{agent_id}] CLI exited with code {proc.returncode}", flush=True)
        if err_output:
            print(f"  [{agent_id}] stderr: {err_output[:500]}", flush=True)

    print(f"  [{agent_id}] Output: {len(output)} chars", flush=True)
    return output
