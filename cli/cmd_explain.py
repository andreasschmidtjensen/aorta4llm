"""aorta explain — verbose trace of governance evaluation for debugging."""

import yaml

from governance.service import GovernanceService
from integration.hooks import TOOL_ACTION_MAP


def add_parser(subparsers):
    p = subparsers.add_parser("explain", help="Explain why an action is allowed or blocked")
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--tool", help="Tool name (Write, Edit, Bash, Read)")
    p.add_argument("--path", help="File path for Write/Edit/Read")
    p.add_argument("--bash-command", help="Bash command to check")
    p.add_argument("--agent", required=True, help="Agent ID")
    p.add_argument("--role", required=True, help="Role name")
    p.add_argument("--scope", default="", help="Agent scope")
    p.set_defaults(func=run)


def run(args):
    with open(args.org_spec) as f:
        spec = yaml.safe_load(f)

    service = GovernanceService(args.org_spec)
    service.register_agent(args.agent, args.role, args.scope)

    # Determine action and params.
    if not args.tool and not args.bash_command:
        print("Specify --tool and/or --bash-command.")
        raise SystemExit(1)

    action = None
    params: dict = {}
    if args.tool:
        action = TOOL_ACTION_MAP.get(args.tool)
        if not action:
            print(f"Unknown tool: {args.tool}")
            raise SystemExit(1)
        if args.tool == "Bash" and args.bash_command:
            params["command"] = args.bash_command
        elif args.path:
            params["path"] = args.path
    elif args.bash_command:
        action = "execute_command"
        params["command"] = args.bash_command

    print(f"=== Governance Explanation ===")
    print(f"Org spec: {args.org_spec}")
    print(f"Agent:    {args.agent} (role: {args.role}, scope: {args.scope or 'unrestricted'})")
    print(f"Action:   {action}({params.get('path', params.get('command', ''))})")
    print()

    # Show all norms from the spec.
    norms = spec.get("norms", [])
    print(f"Norms ({len(norms)}):")
    for i, norm in enumerate(norms):
        ntype = norm.get("type", "?")
        role = norm.get("role", "?")
        severity = f" [{norm['severity']}]" if norm.get("severity") else ""
        print(f"  #{i+1} {ntype} (role: {role}){severity}")
        _print_norm_detail(norm)
    print()

    # Run the check.
    result = service.check_permission(args.agent, args.role, action, params)

    # Show evaluation results per norm.
    print("Evaluation:")
    for i, norm in enumerate(norms):
        relevance = _check_norm_relevance(norm, args.role, action, params)
        status = relevance["status"]
        reason = relevance["reason"]
        symbol = {"skip": "  ", "match": ">>", "no_match": "  "}[status]
        marker = {"skip": "SKIP", "match": "MATCH", "no_match": "PASS"}[status]
        print(f"  {symbol} #{i+1} [{marker}] {reason}")
    print()

    # Final decision.
    symbol = "APPROVE" if result.permitted else "BLOCK"
    print(f"Decision: {symbol}")
    if result.reason:
        print(f"Reason:   {result.reason}")


def _print_norm_detail(norm: dict) -> None:
    """Print norm-specific details."""
    ntype = norm.get("type")
    if ntype == "scope":
        print(f"        paths: {norm.get('paths', [])}")
    elif ntype == "protected":
        print(f"        paths: {norm.get('paths', [])}")
    elif ntype == "forbidden_paths":
        print(f"        paths: {norm.get('paths', [])}")
    elif ntype == "forbidden_command":
        print(f"        pattern: '{norm.get('command_pattern', '')}'")
    elif ntype == "required_before":
        print(f"        pattern: '{norm.get('command_pattern', '')}', requires: {norm.get('requires')}")


def _check_norm_relevance(norm: dict, role: str, action: str, params: dict) -> dict:
    """Check if a norm is relevant to this action and whether it matches."""
    ntype = norm.get("type")
    norm_role = norm.get("role", "")

    if norm_role != role:
        return {"status": "skip", "reason": f"role '{norm_role}' != '{role}'"}

    path = params.get("path", "")
    command = params.get("command", "")

    if ntype == "scope":
        if action not in ("write_file",):
            return {"status": "skip", "reason": "scope only applies to write_file"}
        paths = norm.get("paths", [])
        for scope in paths:
            scope_normalized = scope.rstrip("/") + "/"
            if path.startswith(scope_normalized):
                return {"status": "no_match", "reason": f"path '{path}' is inside scope '{scope}'"}
        return {"status": "match", "reason": f"path '{path}' is outside scope {paths}"}

    if ntype == "protected":
        if action not in ("read_file", "write_file"):
            return {"status": "skip", "reason": "protected applies to read/write only"}
        for p in norm.get("paths", []):
            prefix = p.rstrip("/")
            if path == prefix or path.startswith(prefix):
                return {"status": "match", "reason": f"path '{path}' matches protected prefix '{p}'"}
        return {"status": "no_match", "reason": f"path '{path}' does not match any protected prefix"}

    if ntype == "forbidden_paths":
        if action != "write_file":
            return {"status": "skip", "reason": "forbidden_paths only applies to write_file"}
        for p in norm.get("paths", []):
            if path.startswith(p):
                return {"status": "match", "reason": f"path '{path}' matches forbidden prefix '{p}'"}
        return {"status": "no_match", "reason": f"path '{path}' does not match any forbidden prefix"}

    if ntype == "forbidden_command":
        if action != "execute_command":
            return {"status": "skip", "reason": "forbidden_command only applies to execute_command"}
        pattern = norm.get("command_pattern", "")
        if pattern in command:
            return {"status": "match", "reason": f"command contains '{pattern}'"}
        return {"status": "no_match", "reason": f"command does not contain '{pattern}'"}

    if ntype == "required_before":
        if action != "execute_command":
            return {"status": "skip", "reason": "required_before only applies to execute_command"}
        pattern = norm.get("command_pattern", "")
        if pattern not in command:
            return {"status": "skip", "reason": f"command does not match pattern '{pattern}'"}
        return {"status": "match", "reason": f"command matches '{pattern}', requires '{norm.get('requires')}'"}

    return {"status": "skip", "reason": f"unknown norm type '{ntype}'"}
