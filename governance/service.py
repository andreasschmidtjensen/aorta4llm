"""Governance service — high-level API wrapping the engine."""

import json
import sys
from pathlib import Path

from governance.compiler import compile_org_spec
from governance.engine import GovernanceEngine, PermissionResult


class GovernanceService:
    """High-level service wrapping the governance engine."""

    def __init__(self, org_spec_path: str | Path):
        self._engine = GovernanceEngine()
        spec = compile_org_spec(org_spec_path)
        self._engine.load_org_spec(spec)
        self._agent_scopes: dict[str, str] = {}

    def register_agent(self, agent: str, role: str, scope: str = ""):
        """Register an agent with a role and assigned scope."""
        self._engine.enact_role(agent, role)
        self._agent_scopes[agent] = scope

    def check_permission(
        self, agent: str, role: str, action: str, params: dict | None = None
    ) -> PermissionResult:
        """Check whether an action is permitted, injecting stored scope."""
        if params is None:
            params = {}
        # Inject stored scope if not explicitly provided
        if "scope" not in params and agent in self._agent_scopes:
            params["scope"] = self._agent_scopes[agent]
        return self._engine.check_permission(agent, role, action, params)


def run_stdio_service(org_spec_path: str):
    """Run a JSON-lines stdin/stdout service loop."""
    service = GovernanceService(org_spec_path)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            _respond({"error": str(e)})
            continue

        method = request.get("method")

        if method == "register_agent":
            service.register_agent(
                request["agent"], request["role"], request.get("scope", "")
            )
            _respond({"ok": True})

        elif method == "check_permission":
            result = service.check_permission(
                request["agent"],
                request["role"],
                request["action"],
                request.get("params", {}),
            )
            _respond({
                "permitted": result.permitted,
                "reason": result.reason,
                "violation": result.violation,
            })

        else:
            _respond({"error": f"unknown method: {method}"})


def _respond(obj: dict):
    """Write a JSON response to stdout."""
    print(json.dumps(obj), flush=True)
