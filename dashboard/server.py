"""Governance dashboard — live monitoring web UI for aorta4llm.

Serves a single-page dashboard that shows agents, permission checks,
obligations, and organizational norms in real time via SSE. Can also
launch orchestrator workflows from the UI.

Usage:
    uv run python -m dashboard.server --org-spec org-specs/three_role_workflow.yaml
    # Open http://localhost:5111
"""

import argparse
import asyncio
import json
import threading
import time
import traceback
import uuid
from pathlib import Path

import yaml
from flask import Flask, Response, jsonify, send_from_directory, request

from integration.events import read_events

app = Flask(__name__, static_folder="static")

_org_spec: dict = {}
_org_spec_path: str = ""
_events_path: Path = Path(".aorta/events.jsonl")
_cwd: str = "."

# Workflow state (one workflow at a time)
_workflow: dict = {
    "id": None,
    "status": "idle",   # idle | running | complete | error
    "phase": None,
    "error": None,
    "thread": None,
}


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


# --- Orchestrator workflow API ---

@app.route("/api/workflow/start", methods=["POST"])
def api_workflow_start():
    """Start an orchestrator workflow in a background thread."""
    global _workflow

    if _workflow["status"] == "running":
        return jsonify({"error": "A workflow is already running"}), 409

    data = request.get_json() or {}
    task = data.get("task", "").strip()
    scope = data.get("scope", "").strip()
    model = data.get("model", "sonnet")
    max_turns = data.get("max_turns", 10)
    project_dir = data.get("project_dir", "").strip() or _cwd

    if not task:
        return jsonify({"error": "task is required"}), 400
    if not scope:
        return jsonify({"error": "scope is required"}), 400

    # Clear stale events and state from previous runs
    if _events_path.exists():
        _events_path.unlink()
    state_file = _events_path.parent / "state.json"
    if state_file.exists():
        state_file.unlink()

    workflow_id = str(uuid.uuid4())[:8]

    _workflow = {
        "id": workflow_id,
        "status": "running",
        "phase": None,
        "error": None,
        "thread": None,
    }

    thread = threading.Thread(
        target=_run_workflow_bg,
        args=(workflow_id, task, scope, model, max_turns, project_dir),
        daemon=True,
    )
    _workflow["thread"] = thread
    thread.start()

    return jsonify({"workflow_id": workflow_id, "status": "running"})


@app.route("/api/workflow/status")
def api_workflow_status():
    """Get the current workflow status."""
    return jsonify({
        "workflow_id": _workflow["id"],
        "status": _workflow["status"],
        "phase": _workflow["phase"],
        "error": _workflow["error"],
    })


def _run_workflow_bg(workflow_id: str, task: str, scope: str, model: str, max_turns: int, project_dir: str):
    """Run the orchestrator workflow in a background thread."""
    global _workflow

    from integration.events import log_event
    from orchestrator.run import run_workflow

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_workflow(
                org_spec_path=_org_spec_path,
                task=task,
                scope=scope,
                model=model,
                cwd=project_dir,
                max_turns=max_turns,
                events_path=_events_path,
            ))
        finally:
            loop.close()

        _workflow["status"] = "complete"

    except Exception as e:
        _workflow["status"] = "error"
        _workflow["error"] = str(e)
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        prog="dashboard.server",
        description="aorta4llm governance dashboard",
    )
    parser.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    parser.add_argument("--events", default=".aorta/events.jsonl", help="Events JSONL path")
    parser.add_argument("--port", default=5111, type=int, help="Server port")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--cwd", default=".", help="Target project directory for orchestrator")

    args = parser.parse_args()

    global _org_spec, _org_spec_path, _events_path, _cwd
    _events_path = Path(args.events)
    _org_spec_path = args.org_spec
    _cwd = args.cwd
    with open(args.org_spec) as f:
        _org_spec = yaml.safe_load(f)

    print(f"Dashboard: http://{args.host}:{args.port}")
    print(f"Org spec:  {args.org_spec}")
    print(f"Events:    {args.events}")
    print(f"Project:   {args.cwd}")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
