"""Append-only event log for governance visibility.

Events are written as JSON lines to .aorta/events.jsonl. Each event gets
a timestamp added automatically. The file is the shared data layer for
the web dashboard, future statusLine, and any other monitoring tool.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_EVENTS_PATH = Path(".aorta/events.jsonl")


def log_event(event: dict, events_path: Path = _DEFAULT_EVENTS_PATH) -> None:
    """Append a timestamped event to the JSONL log.

    Args:
        event: Event dict (must have "type" key). "ts" is added automatically.
        events_path: Path to the events file.
    """
    events_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with open(events_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def read_events(events_path: Path = _DEFAULT_EVENTS_PATH, limit: int = 200) -> list[dict]:
    """Read the most recent events from the JSONL log.

    Args:
        events_path: Path to the events file.
        limit: Maximum number of events to return (most recent).

    Returns:
        List of event dicts, oldest first.
    """
    if not events_path.exists():
        return []
    lines = events_path.read_text().strip().splitlines()
    recent = lines[-limit:] if limit else lines
    events = []
    for line in recent:
        if line.strip():
            events.append(json.loads(line))
    return events
