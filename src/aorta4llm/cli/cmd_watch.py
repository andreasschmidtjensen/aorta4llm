"""aorta watch — live tail of governance events with counts-as support."""

import json
import os
import time
from collections import deque
from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("watch", help="Live tail of governance events")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--events-path", default=None, help="Events JSONL path (default: .aorta/events.jsonl)")
    p.add_argument("--since", type=int, default=0, help="Show events from last N seconds (0 = all)")
    p.add_argument("--dashboard", action="store_true", help="Split-panel dashboard with policy tree and event stream")
    p.set_defaults(func=run)


_SYMBOLS = {
    "approve": "\033[32m✓\033[0m",   # green checkmark
    "block": "\033[31m✗\033[0m",      # red cross
}

_SEVERITY_COLORS = {
    "soft": "\033[33m",   # yellow
    "hard": "\033[31m",   # red
}

_RESET = "\033[0m"


def _format_event(event: dict) -> str | None:
    etype = event.get("type")
    ts = event.get("ts", "")
    if isinstance(ts, str) and "T" in ts:
        ts = ts.split("T")[1].split("+")[0].split(".")[0]

    if etype == "register":
        agent = event.get("agent", "?")
        role = event.get("role", "?")
        scope = event.get("scope", "")
        reinit = " (reinit)" if event.get("reinit") else ""
        return f"{ts} \033[36mREGISTER\033[0m {agent} as {role} scope={scope}{reinit}"

    if etype == "check":
        decision = event.get("decision", "?")
        symbol = _SYMBOLS.get(decision, "?")
        agent = event.get("agent", "?")
        action = event.get("action", "?")
        path = event.get("path", "")
        severity = event.get("severity", "")
        reason = event.get("reason", "")

        target = path or reason.split("(")[0] if reason else ""
        sev_str = ""
        if severity:
            color = _SEVERITY_COLORS.get(severity, "")
            sev_str = f" {color}[{severity}]{_RESET}"

        command = event.get("command", "")
        line = f"{ts} {symbol} {agent} {action}"
        if path:
            line += f" {path}"
        elif command:
            cmd_short = command if len(command) <= 40 else command[:37] + "..."
            line += f" {cmd_short}"
        line += sev_str
        if decision == "block" and reason:
            short_reason = reason.split("\n")[0]
            if len(short_reason) > 80:
                short_reason = short_reason[:77] + "..."
            line += f" — {short_reason}"
        if decision == "approve" and reason:
            short_reason = reason.split("\n")[0]
            if len(short_reason) > 80:
                short_reason = short_reason[:77] + "..."
            line += f" — {short_reason}"
        return line

    if etype == "achieved":
        agent = event.get("agent", "?")
        mark = event.get("mark", "?")
        return f"{ts} \033[35m★\033[0m {agent} achieved {mark}"

    if etype == "achievement_reset":
        agent = event.get("agent", "?")
        mark = event.get("mark", "?")
        return f"{ts} \033[33m↺\033[0m {agent} reset {mark}"

    if etype == "achievement_cleared":
        agent = event.get("agent", "?")
        mark = event.get("mark", "?")
        reason = event.get("reason", "")
        suffix = f" ({reason})" if reason else ""
        return f"{ts} \033[33m✗\033[0m {agent} cleared {mark}{suffix}"

    if etype == "counts_as":
        agent = event.get("agent", "?")
        mark = event.get("mark", "?")
        when = event.get("when", [])
        return f"{ts} \033[35m★\033[0m {agent} counts-as {mark} (from {', '.join(when)})"

    if etype == "counts_as_obligation":
        agent = event.get("agent", "?")
        objective = event.get("objective", "?")
        when = event.get("when", [])
        return f"{ts} \033[33m!\033[0m {agent} obligation {objective} (from {', '.join(when)})"

    if etype == "obligation_created":
        agent = event.get("agent", "?")
        objective = event.get("objective", "?")
        deadline = event.get("deadline", "false")
        dl_str = f" deadline={deadline}" if deadline != "false" else ""
        return f"{ts} \033[33m!\033[0m {agent} obliged {objective}{dl_str}"

    if etype == "allow_once":
        path = event.get("path", "?")
        agent = event.get("agent", "*")
        agent_str = f" (agent: {agent})" if agent != "*" else ""
        return f"{ts} \033[33m⚑\033[0m allow-once {path}{agent_str}"

    if etype == "bash_analysis":
        decision = event.get("decision", "?")
        symbol = _SYMBOLS.get(decision, "?")
        agent = event.get("agent", "?")
        writes = event.get("writes", [])
        return f"{ts} {symbol} {agent} bash writes: {writes}"

    if etype == "violation":
        agent = event.get("agent", "?")
        action = event.get("action", "?")
        count = event.get("count", "?")
        reason = event.get("reason", "")
        short = reason[:60] + "..." if len(reason) > 60 else reason
        return f"{ts} \033[31m⚠\033[0m {agent} violation #{count} ({action}) {short}"

    if etype == "sanction":
        agent = event.get("agent", "?")
        sanction = event.get("sanction", "?")
        threshold = event.get("threshold", "?")
        if sanction == "hold":
            reason = event.get("reason", "")
            return f"{ts} \033[31m⛔\033[0m {agent} sanction: hold after {threshold} violations — {reason}"
        if sanction == "obliged":
            objective = event.get("objective", "?")
            return f"{ts} \033[33m⚠\033[0m {agent} sanction: obliged {objective} after {threshold} violations"
        return f"{ts} \033[31m⚠\033[0m {agent} sanction: {sanction} after {threshold} violations"

    # Fallback: show unrecognized events raw so nothing is silently dropped.
    return f"{ts} \033[90m{etype}\033[0m {json.dumps({k: v for k, v in event.items() if k not in ('ts', 'type', 'org_spec')})}"


class _Dashboard:
    """ANSI split-panel dashboard: policy tree (left) + event stream (right)."""

    def __init__(self, spec: dict, state_path: Path, events_path: Path,
                 max_events: int = 100):
        self._spec = spec
        self._state_path = state_path
        self._events_path = events_path
        self._event_buffer: deque[str] = deque(maxlen=max_events)
        self._last_state_mtime: float = 0

    def _get_terminal_size(self) -> tuple[int, int]:
        try:
            size = os.get_terminal_size()
            return size.columns, size.lines
        except OSError:
            return 80, 24

    def _load_achievements(self) -> list[str]:
        if not self._state_path.exists():
            return []
        try:
            state = json.loads(self._state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        achievements = []
        for event in state.get("events", []):
            if event.get("type") == "achieved":
                achievements.extend(event.get("objectives", []))
        return achievements

    def _render_policy_lines(self, height: int) -> list[str]:
        """Render the policy panel as a list of plain-text lines."""
        lines: list[str] = []
        achievements = self._load_achievements()
        achieved_set = set(achievements)

        lines.append(f"\033[1m{self._spec.get('organization', '?')}\033[0m")

        access = self._spec.get("access", {})
        if access:
            lines.append("")
            lines.append("\033[4mAccess\033[0m")
            for path, level in access.items():
                lines.append(f"  {path:<16s} {level}")

        norms = self._spec.get("norms", [])
        if norms:
            lines.append("")
            lines.append("\033[4mNorms\033[0m")
            for norm in norms:
                ntype = norm.get("type", "?")
                sev = norm.get("severity", "")
                if ntype == "forbidden_command":
                    tag = f"[{sev}]" if sev else "[hard]"
                    lines.append(f"  {tag} {norm.get('command_pattern', '?')}")
                elif ntype == "required_before":
                    lines.append(f"  gate: {norm.get('requires', '?')} -> {norm.get('command_pattern', '?')}")
                else:
                    lines.append(f"  {ntype}")

        triggers = self._spec.get("achievement_triggers", [])
        if triggers:
            lines.append("")
            lines.append("\033[4mAchievements\033[0m")
            for trigger in triggers:
                mark = trigger.get("marks", "?")
                marker = "\033[32m*\033[0m" if mark in achieved_set else "\033[90mo\033[0m"
                lines.append(f"  {marker} {mark}")

        return lines[:height]

    def render(self) -> str:
        """Render the full dashboard frame."""
        cols, rows = self._get_terminal_size()
        left_width = max(int(cols * 0.4), 20)
        right_width = cols - left_width - 3  # 3 for separator

        # Build left panel
        content_rows = rows - 2  # header + footer
        left_lines = self._render_policy_lines(content_rows)

        # Build right panel from event buffer
        right_lines = list(self._event_buffer)[-content_rows:]

        # Compose frame
        output_lines: list[str] = []
        # Header
        left_header = " Policy".ljust(left_width)
        right_header = " Events".ljust(right_width)
        output_lines.append(f"\033[7m{left_header}\033[0m | \033[7m{right_header}\033[0m")

        for i in range(content_rows):
            left = _strip_pad(left_lines[i] if i < len(left_lines) else "", left_width)
            right = _strip_pad(right_lines[i] if i < len(right_lines) else "", right_width)
            output_lines.append(f"{left} \033[90m│\033[0m {right}")

        return "\n".join(output_lines)

    def add_event(self, event: dict):
        formatted = _format_event(event)
        if formatted:
            self._event_buffer.append(formatted)

    def run_loop(self):
        """Main loop: tail events, re-render on changes."""
        # Load existing events
        if self._events_path.exists():
            with open(self._events_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.add_event(json.loads(line))
                    except json.JSONDecodeError:
                        pass

        # Initial render
        print("\033[2J\033[H", end="")  # clear screen
        print(self.render())

        # Tail for new events
        try:
            with open(self._events_path) as f:
                f.seek(0, 2)
                while True:
                    line = f.readline()
                    if line:
                        line = line.strip()
                        if line:
                            try:
                                self.add_event(json.loads(line))
                                print("\033[H", end="")  # cursor to top
                                print(self.render())
                            except json.JSONDecodeError:
                                pass
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nStopped.")


def _strip_ansi_len(s: str) -> int:
    """Get the visible length of a string (strip ANSI codes)."""
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def _strip_pad(s: str, width: int) -> str:
    """Pad or truncate a string to exactly `width` visible characters."""
    visible_len = _strip_ansi_len(s)
    if visible_len >= width:
        # Truncate: walk through, count visible chars
        result = []
        count = 0
        i = 0
        while i < len(s) and count < width:
            if s[i] == "\033":
                # Consume ANSI sequence
                j = i + 1
                while j < len(s) and s[j] != "m":
                    j += 1
                result.append(s[i:j + 1])
                i = j + 1
            else:
                result.append(s[i])
                count += 1
                i += 1
        return "".join(result) + "\033[0m"
    return s + " " * (width - visible_len)


def run(args):
    from aorta4llm.cli.spec_utils import find_org_spec

    org_spec_path = find_org_spec(args.org_spec)
    events_path = Path(args.events_path) if args.events_path else (
        org_spec_path.parent / "events.jsonl"
    )

    if not events_path.exists():
        print(f"No events file at {events_path}")
        print("Run 'aorta init' first or check the path.")
        raise SystemExit(1)

    if args.dashboard:
        import yaml
        from aorta4llm.governance.compiler import _resolve_includes
        from aorta4llm.integration.hooks import _default_state_path
        with open(org_spec_path) as f:
            spec = yaml.safe_load(f)
        resolved = _resolve_includes(dict(spec))
        state_path = _default_state_path(str(org_spec_path))
        dashboard = _Dashboard(resolved, state_path, events_path)
        dashboard.run_loop()
        return

    print(f"Watching {events_path} (Ctrl+C to stop)")
    print("─" * 60)

    # Show existing events (optionally filtered by --since)
    now = time.time()
    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            formatted = _format_event(event)
            if formatted:
                print(formatted)

    # Tail for new events
    try:
        with open(events_path) as f:
            f.seek(0, 2)  # seek to end
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line:
                        try:
                            event = json.loads(line)
                            formatted = _format_event(event)
                            if formatted:
                                print(formatted)
                        except json.JSONDecodeError:
                            pass
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.")
