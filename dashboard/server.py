"""Governance dashboard — live monitoring web UI for aorta4llm.

Serves a single-page dashboard that shows agents, permission checks,
obligations, and organizational norms in real time via SSE.

Usage:
    uv run python -m dashboard.server --org-spec org-specs/three_role_workflow.yaml
    # Open http://localhost:5111
"""

import argparse
import json
import time
from pathlib import Path

import yaml
from flask import Flask, Response, jsonify, send_from_directory, request

from integration.events import read_events

app = Flask(__name__, static_folder="static")

_org_spec: dict = {}
_org_spec_path: str = ""
_events_path: Path = Path(".aorta/events.jsonl")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/org-spec")
def api_org_spec():
    return jsonify(_org_spec)


@app.route("/api/state")
def api_state():
    """Derive current state from events: agents + active obligations."""
    events = read_events(_events_path, limit=0)

    agents: dict[str, dict] = {}
    obligations: dict[str, dict] = {}
    stats = {"checks": 0, "approved": 0, "blocked": 0}

    for ev in events:
        etype = ev.get("type")
        if etype == "register":
            agents[ev["agent"]] = {
                "agent": ev["agent"],
                "role": ev["role"],
                "scope": ev.get("scope", ""),
                "registered_at": ev.get("ts", ""),
            }
        elif etype == "check":
            stats["checks"] += 1
            if ev.get("decision") == "approve":
                stats["approved"] += 1
            else:
                stats["blocked"] += 1
        elif etype == "norm_change":
            key = f"{ev.get('agent')}:{ev.get('objective')}"
            change = ev.get("change")
            if change == "activated":
                obligations[key] = {
                    "agent": ev.get("agent", ""),
                    "deontic": ev.get("deontic", ""),
                    "objective": ev.get("objective", ""),
                    "deadline": ev.get("deadline", ""),
                    "status": "active",
                    "activated_at": ev.get("ts", ""),
                }
            elif change in ("fulfilled", "violated") and key in obligations:
                obligations[key]["status"] = change

    active_obligations = [o for o in obligations.values() if o["status"] == "active"]

    return jsonify({
        "agents": list(agents.values()),
        "obligations": active_obligations,
        "stats": stats,
    })


@app.route("/api/events")
def api_events():
    limit = request.args.get("limit", 100, type=int)
    events = read_events(_events_path, limit=limit)
    return jsonify(events)


@app.route("/api/events/stream")
def api_event_stream():
    """SSE endpoint that tails the events.jsonl file."""
    def generate():
        last_pos = 0
        if _events_path.exists():
            last_pos = _events_path.stat().st_size

        # Send initial keepalive
        yield ": keepalive\n\n"

        while True:
            if _events_path.exists():
                current_size = _events_path.stat().st_size
                if current_size > last_pos:
                    with open(_events_path) as f:
                        f.seek(last_pos)
                        new_data = f.read()
                        last_pos = f.tell()
                    for line in new_data.strip().splitlines():
                        if line.strip():
                            yield f"data: {line}\n\n"
                elif current_size < last_pos:
                    # File was truncated/recreated
                    last_pos = 0
                    continue
            time.sleep(0.5)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})



def main():
    parser = argparse.ArgumentParser(
        prog="dashboard.server",
        description="aorta4llm governance dashboard",
    )
    parser.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    parser.add_argument("--events", default=".aorta/events.jsonl", help="Events JSONL path")
    parser.add_argument("--port", default=5111, type=int, help="Server port")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    args = parser.parse_args()

    global _org_spec, _org_spec_path, _events_path
    _events_path = Path(args.events)
    _org_spec_path = args.org_spec
    with open(args.org_spec) as f:
        _org_spec = yaml.safe_load(f)

    print(f"Dashboard: http://{args.host}:{args.port}")
    print(f"Org spec:  {args.org_spec}")
    print(f"Events:    {args.events}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
