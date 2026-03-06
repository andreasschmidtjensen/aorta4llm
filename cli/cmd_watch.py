"""aorta watch — live tail of governance events."""

import json
import time
from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("watch", help="Live tail of governance events")
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--events-path", default=None, help="Events JSONL path (default: .aorta/events.jsonl)")
    p.add_argument("--since", type=int, default=0, help="Show events from last N seconds (0 = all)")
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

        line = f"{ts} {symbol} {agent} {action}"
        if path:
            line += f" {path}"
        line += sev_str
        if decision == "block" and reason:
            short_reason = reason.split("\n")[0]
            if len(short_reason) > 80:
                short_reason = short_reason[:77] + "..."
            line += f" — {short_reason}"
        return line

    if etype == "achieved":
        agent = event.get("agent", "?")
        mark = event.get("mark", "?")
        return f"{ts} \033[35m★\033[0m {agent} achieved {mark}"

    if etype == "bash_analysis":
        decision = event.get("decision", "?")
        symbol = _SYMBOLS.get(decision, "?")
        agent = event.get("agent", "?")
        writes = event.get("writes", [])
        return f"{ts} {symbol} {agent} bash writes: {writes}"

    return None


def run(args):
    events_path = Path(args.events_path) if args.events_path else (
        Path(args.org_spec).parent / "events.jsonl"
    )

    if not events_path.exists():
        print(f"No events file at {events_path}")
        print("Run 'aorta init' first or check the path.")
        raise SystemExit(1)

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
