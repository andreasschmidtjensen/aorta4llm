"""aorta replay — validate policies against real session traces."""

import hashlib
import json
import os
from pathlib import Path


def add_parser(subparsers):
    p = subparsers.add_parser("replay", help="Replay a session trace against governance policy")
    p.add_argument("--spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--trace", default=None, help="Path to session JSONL trace file")
    p.add_argument("--last", action="store_true", help="Use the most recent session trace")
    p.add_argument("--all", action="store_true", help="Replay all session traces")
    p.add_argument("--format", choices=["summary", "full", "json"], default="summary", dest="output_format",
                   help="Output format (default: summary)")
    p.add_argument("--agent", default="agent", help="Agent name (default: agent)")
    p.set_defaults(func=run)


def _encode_project_path(cwd: str) -> str:
    """Encode cwd the way Claude Code does for project directories."""
    # Claude Code uses the cwd path with / replaced by -
    return cwd.replace("/", "-").lstrip("-")


def _find_sessions_dir() -> Path | None:
    """Find the Claude Code sessions directory for the current project."""
    home = Path.home()
    claude_dir = home / ".claude" / "projects"
    if not claude_dir.is_dir():
        return None
    encoded = _encode_project_path(os.getcwd())
    project_dir = claude_dir / encoded
    if project_dir.is_dir():
        return project_dir
    return None


def _find_session_traces(sessions_dir: Path) -> list[Path]:
    """Find all session JSONL files, sorted newest first."""
    traces = list(sessions_dir.glob("*.jsonl"))
    traces.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return traces


def _tool_detail(r) -> str:
    """Format tool name + target for display."""
    inp = r.tool_input
    detail = ""
    if r.tool_name in ("Write", "Edit", "Read"):
        detail = f" {inp.get('file_path', '')}"
    elif r.tool_name == "Bash":
        cmd = inp.get("command", "")
        detail = f" {cmd[:50]}" if cmd else ""
    return f"{r.tool_name}{detail}"


def _print_summary(results: list, trace_path: Path):
    """Print a summary of replay results."""
    total = len(results)
    policy_blocks = sum(1 for r in results if r.block_type == "policy")
    sanctions = sum(1 for r in results if r.block_type == "sanction")
    held = sum(1 for r in results if r.block_type == "held")
    blocked = policy_blocks + sanctions + held
    approved = total - blocked

    print(f"Trace: {trace_path}")
    print(f"  Events: {total}  Approved: {approved}  Blocked: {blocked}")
    if policy_blocks:
        print(f"    Policy blocks:   {policy_blocks}")
    if sanctions:
        print(f"    Sanctions:       {sanctions}")
    if held:
        print(f"    Held (counterfactual): {held}")

    # Show unique policy blocks (deduplicated by reason)
    seen_reasons: set[str] = set()
    unique_blocks = []
    for r in results:
        if r.block_type == "policy":
            short = r.pre_reason.split("\n")[0][:80]
            if short not in seen_reasons:
                seen_reasons.add(short)
                unique_blocks.append(r)

    if unique_blocks:
        print(f"\n  Policy blocks ({len(unique_blocks)} unique):")
        for r in unique_blocks:
            reason = r.pre_reason.split("\n")[0][:60]
            sc = " [sidechain]" if r.is_sidechain else ""
            print(f"    {_tool_detail(r)}{sc}")
            if reason:
                print(f"      {reason}")

    # Show divergence points (sanctions)
    for i, r in enumerate(results):
        if r.block_type == "sanction":
            reason = r.pre_reason.split("\n")[0]
            print(f"\n  Divergence at event {i + 1}: {reason}")


def _print_full(results: list, trace_path: Path):
    """Print full details of each replay result."""
    print(f"Trace: {trace_path}")
    print("─" * 60)
    in_counterfactual = False
    for i, r in enumerate(results):
        # Mark divergence points
        if r.block_type == "sanction" and not in_counterfactual:
            in_counterfactual = True
            print("┄┄┄ divergence: hold triggered ┄┄┄")

        prefix = "  " if in_counterfactual else ""
        if in_counterfactual and r.block_type == "held":
            symbol = "·"  # muted — wouldn't have happened
        else:
            symbol = "✓" if r.pre_decision == "approve" else "✗"

        sc = " [sidechain]" if r.is_sidechain else ""
        print(f"{prefix}{symbol} {_tool_detail(r)}{sc}")
        if r.block_type == "policy":
            reason = r.pre_reason.split("\n")[0][:80]
            print(f"{prefix}  reason: {reason}")
        elif r.block_type == "sanction":
            reason = r.pre_reason.split("\n")[0]
            # Avoid double "SANCTION:" prefix
            if reason.startswith("SANCTION: "):
                reason = reason[len("SANCTION: "):]
            print(f"{prefix}  SANCTION: {reason}")
        if r.post_result and r.post_result.get("achievements"):
            print(f"{prefix}  achieved: {r.post_result['achievements']}")


def _print_json(results: list, trace_path: Path):
    """Print replay results as JSON."""
    policy_blocks = sum(1 for r in results if r.block_type == "policy")
    sanctions = sum(1 for r in results if r.block_type == "sanction")
    held = sum(1 for r in results if r.block_type == "held")
    divergence_at = None
    for i, r in enumerate(results):
        if r.block_type == "sanction":
            divergence_at = i + 1
            break

    output = {
        "trace": str(trace_path),
        "total": len(results),
        "approved": sum(1 for r in results if r.pre_decision == "approve"),
        "blocked": {
            "total": policy_blocks + sanctions + held,
            "policy": policy_blocks,
            "sanctions": sanctions,
            "held_counterfactual": held,
        },
        "divergence_at": divergence_at,
        "events": [
            {
                "tool": r.tool_name,
                "decision": r.pre_decision,
                "block_type": r.block_type,
                "reason": r.pre_reason,
                "sidechain": r.is_sidechain,
            }
            for r in results
        ],
    }
    print(json.dumps(output, indent=2))


def run(args):
    from aorta4llm.cli.spec_utils import find_org_spec
    from aorta4llm.replay.trace_parser import parse_trace
    from aorta4llm.replay.engine import ReplayEngine

    org_spec_path = find_org_spec(args.spec)

    # Determine trace files to replay
    trace_paths: list[Path] = []
    if args.trace:
        trace_paths.append(Path(args.trace))
    elif args.last or args.all:
        sessions_dir = _find_sessions_dir()
        if not sessions_dir:
            print("No Claude Code sessions directory found for this project.")
            raise SystemExit(1)
        traces = _find_session_traces(sessions_dir)
        if not traces:
            print(f"No session traces found in {sessions_dir}")
            raise SystemExit(1)
        if args.last:
            trace_paths.append(traces[0])
        else:
            trace_paths = traces
    else:
        print("Specify --trace <path>, --last, or --all")
        raise SystemExit(1)

    printer = {
        "summary": _print_summary,
        "full": _print_full,
        "json": _print_json,
    }[args.output_format]

    for trace_path in trace_paths:
        if not trace_path.exists():
            print(f"Trace file not found: {trace_path}")
            continue
        events = parse_trace(trace_path)
        if not events:
            print(f"No tool events found in {trace_path}")
            continue
        engine = ReplayEngine(org_spec_path, agent=args.agent)
        results = engine.replay(events)
        printer(results, trace_path)
        if len(trace_paths) > 1:
            print()
