"""aorta timing — show hook latency statistics."""

import statistics
from pathlib import Path

from aorta4llm.integration.events import read_events


def add_parser(subparsers):
    p = subparsers.add_parser("timing", help="Show hook latency statistics")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--events-path", default=None, help="Events JSONL path")
    p.add_argument("-n", "--last", type=int, default=100, help="Number of recent events (default: 100)")
    p.set_defaults(func=run)


def _percentile(values: list[float], p: int) -> float:
    """Compute the p-th percentile of a sorted list."""
    if not values:
        return 0.0
    k = (len(values) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(values):
        return values[f]
    return values[f] + (k - f) * (values[c] - values[f])


def run(args):
    events_path = _resolve_events_path(args)
    events = read_events(events_path, limit=0)  # read all, then filter

    timing_events = [e for e in events if e.get("type") == "timing"]
    if not timing_events:
        print("No timing data yet. Hook invocations will record timing automatically.")
        return

    # Take the last N
    timing_events = timing_events[-args.last:]

    # Group by command
    by_cmd: dict[str, list[dict]] = {}
    for e in timing_events:
        cmd = e.get("command", "unknown")
        by_cmd.setdefault(cmd, []).append(e)

    print(f"Hook timing (last {len(timing_events)} invocations):\n")

    for cmd in sorted(by_cmd):
        entries = by_cmd[cmd]
        totals = sorted(e.get("total_ms", 0) for e in entries)
        inits = [e.get("init_ms", 0) for e in entries]
        handles = [e.get("handle_ms", 0) for e in entries]

        avg = statistics.mean(totals)
        p50 = _percentile(totals, 50)
        p95 = _percentile(totals, 95)
        mx = max(totals)

        avg_init = statistics.mean(inits)
        avg_handle = statistics.mean(handles)

        print(f"  {cmd:<18} n={len(entries):<4} avg={avg:.0f}ms  p50={p50:.0f}ms  p95={p95:.0f}ms  max={mx:.0f}ms")
        print(f"    {'breakdown':<16} init={avg_init:.0f}ms  handle={avg_handle:.0f}ms")
        print()


def _resolve_events_path(args) -> Path:
    """Resolve the events.jsonl path from args or auto-detect."""
    if args.events_path:
        return Path(args.events_path)

    if args.org_spec:
        return Path(args.org_spec).resolve().parent / "events.jsonl"

    # Auto-detect from .aorta/
    aorta_dir = Path(".aorta")
    if aorta_dir.exists():
        return aorta_dir / "events.jsonl"

    return Path(".aorta/events.jsonl")
