"""aorta dry-run — test governance checks without blocking."""


def add_parser(subparsers):
    p = subparsers.add_parser("dry-run", help="Test governance checks without blocking")
    p.add_argument("--org-spec", default=None, help="Path to org spec YAML (auto-detected from .aorta/)")
    p.add_argument("--tool", help="Tool name (Write, Edit, Bash, Read)")
    p.add_argument("--path", help="File path for Write/Edit/Read")
    p.add_argument("--bash-command", help="Bash command to analyze")
    p.add_argument("--agent", required=True, help="Agent ID")
    p.add_argument("--role", required=True, help="Role name")
    p.add_argument("--scope", default="", help="Agent scope")
    p.set_defaults(func=run)


def _check_self_protection(action: str, path: str) -> str | None:
    """Check hook-layer self-protection rules. Returns block reason or None."""
    from aorta4llm.integration.hooks import PROTECTED_PATHS
    if action == "write_file" and path:
        for protected in PROTECTED_PATHS:
            if path.startswith(protected) or path == protected.rstrip("/"):
                return f"write to '{path}' denied: governance infrastructure is protected"
    return None


def _check_governance_command(command: str) -> str | None:
    """Check if a bash command invokes governance tools. Returns block reason or None."""
    from aorta4llm.integration.hooks import _is_governance_command
    if _is_governance_command(command):
        return "agents cannot run governance commands"
    return None


def _detect_project_root_from_spec(org_spec: str) -> str | None:
    """Detect project root from org spec path (for path normalization in dry-run)."""
    from aorta4llm.integration.hooks import _detect_project_root
    from pathlib import Path
    return _detect_project_root(Path(org_spec))


def _make_path_relative(path: str, org_spec: str) -> str:
    """Normalize absolute paths to relative for dry-run display and checking."""
    import os
    if not path or not os.path.isabs(path):
        return path
    root = _detect_project_root_from_spec(org_spec)
    if root:
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def run(args):
    from aorta4llm.cli.spec_utils import find_org_spec
    from aorta4llm.governance.service import GovernanceService
    from aorta4llm.integration.hooks import TOOL_ACTION_MAP, _normalize_git_cmd

    org_spec = str(find_org_spec(args.org_spec))
    service = GovernanceService(org_spec)
    service.register_agent(args.agent, args.role, args.scope)

    print(f"Agent: {args.agent} (role: {args.role}, scope: {args.scope or 'unrestricted'})")
    print()

    if args.tool:
        action = TOOL_ACTION_MAP.get(args.tool)
        if not action:
            print(f"Unknown tool: {args.tool}")
            print(f"Known tools: {', '.join(sorted(TOOL_ACTION_MAP))}")
            raise SystemExit(1)

        params = {}
        if args.tool == "Bash" and args.bash_command:
            params["command"] = _normalize_git_cmd(args.bash_command)
        elif args.path:
            params["path"] = args.path

        print(f"  Tool:     {args.tool} -> {action}")
        if args.path:
            print(f"  Path:     {args.path}")

        # Check hook-layer self-protection first.
        sp_reason = _check_self_protection(action, params.get("path", ""))
        if sp_reason:
            print(f"  Decision: BLOCK [hook]")
            print(f"  Reason:   {sp_reason}")
        else:
            result = service.check_permission(args.agent, args.role, action, params)
            symbol = "APPROVE" if result.permitted else "BLOCK"
            print(f"  Decision: {symbol}")
            if result.reason:
                print(f"  Reason:   {result.reason}")

    if args.bash_command:
        from aorta4llm.governance.bash_analyzer import analyze_bash_command

        # Check execute_command permission (catches forbidden_command norms).
        if not args.tool:
            print(f"  Bash command: {args.bash_command}")

            # Check hook-layer governance command blocking first.
            gc_reason = _check_governance_command(args.bash_command)
            if gc_reason:
                print(f"  Decision:     BLOCK [hook]")
                print(f"  Reason:       {gc_reason}")
                return

            # Normalize git commands for pattern matching (strips -C <path> etc.)
            normalized_cmd = _normalize_git_cmd(args.bash_command)
            cmd_result = service.check_permission(
                args.agent, args.role, "execute_command",
                {"command": normalized_cmd},
            )
            symbol = "APPROVE" if cmd_result.permitted else "BLOCK"
            severity = f" [{cmd_result.severity}]" if not cmd_result.permitted else ""
            print(f"  Decision:     {symbol}{severity}")
            if cmd_result.reason:
                print(f"  Reason:       {cmd_result.reason}")
            print()

        print(f"  Bash analysis: {args.bash_command}")
        analysis = analyze_bash_command(args.bash_command)
        print(f"  Method:        {analysis.summary}")
        if analysis.writes:
            print(f"  Write paths:   {analysis.writes}")
            for write_path in analysis.writes:
                # Normalize absolute paths to relative before checking scope.
                rel_path = _make_path_relative(write_path, org_spec)
                path_result = service.check_permission(
                    args.agent, args.role, "write_file", {"path": rel_path},
                )
                symbol = "APPROVE" if path_result.permitted else "BLOCK"
                print(f"    write_file({write_path}): {symbol}")
                if path_result.reason:
                    print(f"      Reason: {path_result.reason}")
        else:
            print("  No write paths detected.")

    if not args.tool and not args.bash_command:
        print("Specify --tool and/or --bash-command to test.")
        raise SystemExit(1)
