"""Agent runner — spawns Claude Code CLI with governance enforcement.

Each agent runs as a Claude Code subprocess with all tools available.
Governance is enforced via CLI hooks (PreToolUse) configured in
.claude/settings.local.json. The hook command invokes integration.hooks
which checks permissions against the GovernanceService.

Output is streamed via --output-format stream-json, parsed in real time,
and logged to events.jsonl so the dashboard can show live agent reasoning.
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

from integration.events import log_event

# Default events path
_EVENTS_PATH = Path(".aorta/events.jsonl")

# Truncation limits for events logged to events.jsonl
_MAX_TEXT_EVENT_LEN = 500
_MAX_TOOL_INPUT_EVENT_LEN = 300


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
    project_cwd: str = "",
) -> None:
    """Write .claude/settings.local.json with governance hook config."""
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / "settings.local.json"

    hook_cmd = (
        f"uv run python -m integration.hooks pre-tool-use"
        f" --org-spec {org_spec_path}"
        f" --agent {agent_id}"
        f" --state {state_path}"
        f" --events-path {events_path}"
    )
    if project_cwd:
        hook_cmd += f" --cwd {project_cwd}"

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
                    "command": hook_cmd,
                    "timeout": 10000,
                }],
            }],
        },
    }

    settings_file.write_text(json.dumps(settings, indent=2))


async def _drain_stderr(proc: asyncio.subprocess.Process) -> str:
    """Read stderr fully without blocking stdout processing."""
    data = await proc.stderr.read()
    return data.decode("utf-8", errors="replace").strip()


async def _stream_and_parse(
    proc: asyncio.subprocess.Process,
    agent_id: str,
    role: str,
    events_path: Path,
) -> str:
    """Read stream-json stdout from Claude CLI, emit events, return text.

    Claude Code's stream-json format is JSONL with these message types:
    - system: init metadata (skip)
    - assistant: agent output — content in msg["message"]["content"]
      Content block types: text, thinking, tool_use
    - user: tool results fed back to agent (skip)
    - result: final summary with result text
    - rate_limit_event: rate limiting info (skip)

    Each assistant message line contains one content block from the turn.
    Turn boundaries occur when a user message follows assistant messages.
    """
    collected_text: list[str] = []
    raw_lines: list[str] = []

    turn_count = 0
    parsed_events = 0
    last_msg_id: str = ""
    last_type: str = ""

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()

        if not line:
            continue

        raw_lines.append(line)

        # SSE compat: skip event/keepalive lines, strip data: prefix
        if line.startswith("event:") or line.startswith(":"):
            continue
        json_str = line[6:] if line.startswith("data: ") else line

        try:
            msg = json.loads(json_str)
        except json.JSONDecodeError:
            collected_text.append(line)
            continue

        msg_type = msg.get("type", "")

        # ── Claude Code JSONL format ──────────────────────────

        if msg_type == "system":
            parsed_events += 1

        elif msg_type == "assistant":
            message = msg.get("message", {})
            content = message.get("content", [])
            msg_id = message.get("id", "")

            # New message ID = new turn
            if msg_id and msg_id != last_msg_id:
                if turn_count > 0 and last_type == "user":
                    log_event({
                        "type": "agent_turn_complete",
                        "agent": agent_id,
                        "role": role,
                        "turn": turn_count,
                    }, events_path)
                turn_count += 1
                last_msg_id = msg_id

            for block in content:
                btype = block.get("type")

                if btype == "text":
                    text = block.get("text", "")
                    collected_text.append(text)
                    truncated = text[:_MAX_TEXT_EVENT_LEN]
                    if len(text) > _MAX_TEXT_EVENT_LEN:
                        truncated += f"... ({len(text)} chars)"
                    log_event({
                        "type": "agent_text",
                        "agent": agent_id,
                        "role": role,
                        "text": truncated,
                        "length": len(text),
                        "turn": turn_count,
                    }, events_path)

                elif btype == "thinking":
                    text = block.get("thinking", "")
                    if text:
                        truncated = text[:_MAX_TEXT_EVENT_LEN]
                        if len(text) > _MAX_TEXT_EVENT_LEN:
                            truncated += f"... ({len(text)} chars)"
                        log_event({
                            "type": "agent_text",
                            "agent": agent_id,
                            "role": role,
                            "text": truncated,
                            "length": len(text),
                            "turn": turn_count,
                        }, events_path)

                elif btype == "tool_use":
                    inp = json.dumps(block.get("input", {}))
                    truncated_input = inp[:_MAX_TOOL_INPUT_EVENT_LEN]
                    if len(inp) > _MAX_TOOL_INPUT_EVENT_LEN:
                        truncated_input += "..."
                    log_event({
                        "type": "agent_tool_call",
                        "agent": agent_id,
                        "role": role,
                        "tool": block.get("name", ""),
                        "tool_id": block.get("id", ""),
                        "input_preview": truncated_input,
                        "turn": turn_count,
                    }, events_path)

            parsed_events += 1

        elif msg_type == "user":
            last_type = "user"
            parsed_events += 1
            continue

        elif msg_type == "result":
            result_text = msg.get("result", "")
            if result_text and isinstance(result_text, str):
                collected_text.append(result_text)
                truncated = result_text[:_MAX_TEXT_EVENT_LEN]
                if len(result_text) > _MAX_TEXT_EVENT_LEN:
                    truncated += f"... ({len(result_text)} chars)"
                log_event({
                    "type": "agent_text",
                    "agent": agent_id,
                    "role": role,
                    "text": truncated,
                    "length": len(result_text),
                    "turn": turn_count,
                }, events_path)
            if turn_count > 0:
                log_event({
                    "type": "agent_turn_complete",
                    "agent": agent_id,
                    "role": role,
                    "turn": turn_count,
                }, events_path)
            parsed_events += 1

        elif msg_type in ("rate_limit_event",):
            parsed_events += 1

        # ── SSE streaming format fallback ─────────────────────

        elif msg_type == "message_start":
            turn_count += 1
            parsed_events += 1

        elif msg_type == "message_stop":
            log_event({
                "type": "agent_turn_complete",
                "agent": agent_id,
                "role": role,
                "turn": turn_count,
            }, events_path)
            parsed_events += 1

        last_type = msg_type

    await proc.wait()

    output = "\n".join(collected_text)

    print(f"  [{agent_id}] Stream parser: {len(raw_lines)} lines, "
          f"{parsed_events} structured events, {turn_count} turns", flush=True)

    if not parsed_events and raw_lines:
        fallback_text = "\n".join(raw_lines[:50])
        if len(raw_lines) > 50:
            fallback_text += f"\n... ({len(raw_lines)} lines total)"
        truncated = fallback_text[:_MAX_TEXT_EVENT_LEN]
        if len(fallback_text) > _MAX_TEXT_EVENT_LEN:
            truncated += f"... ({len(fallback_text)} chars)"
        log_event({
            "type": "agent_text",
            "agent": agent_id,
            "role": role,
            "text": truncated,
            "length": len(fallback_text),
            "turn": 0,
        }, events_path)
        output = fallback_text

    debug_dir = events_path.parent
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_file = debug_dir / f"debug-stream-{agent_id}.log"
    debug_file.write_text(
        f"# {agent_id} stream output ({len(raw_lines)} lines, {parsed_events} events)\n"
        + "\n".join(raw_lines[-200:])
    )

    return output


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
    abs_work_dir = str(Path(work_dir).resolve())
    abs_events = str(events_path.resolve())
    abs_state = str(state_path.resolve()) if state_path else str(
        (events_path.parent / "state.json").resolve()
    )
    abs_org_spec = str(Path(org_spec_path).resolve())

    # Write per-agent .claude/settings.local.json with governance hooks
    agent_settings_dir = Path(work_dir) / ".claude"
    _write_hook_settings(agent_settings_dir, abs_org_spec, agent_id, abs_state, abs_events, abs_work_dir)

    # Build CLI command
    cmd = [
        cli,
        "--print",
        "--output-format", "stream-json",
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

    # Read stdout and stderr concurrently to avoid pipe deadlock.
    # If stderr fills its buffer while we block on stdout, the process hangs.
    stderr_task = asyncio.create_task(_drain_stderr(proc))
    output = await _stream_and_parse(proc, agent_id, role, events_path)
    err_output = await stderr_task

    if proc.returncode != 0:
        print(f"  [{agent_id}] CLI exited with code {proc.returncode}", flush=True)
        if err_output:
            print(f"  [{agent_id}] stderr: {err_output[:500]}", flush=True)

    print(f"  [{agent_id}] Output: {len(output)} chars", flush=True)
    return output
