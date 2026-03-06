"""aorta dry-run — test governance checks without blocking."""


def add_parser(subparsers):
    p = subparsers.add_parser("dry-run", help="Test governance checks without blocking")
    p.add_argument("--org-spec", required=True, help="Path to org spec YAML")
    p.add_argument("--tool", help="Tool name (Write, Edit, Bash, Read)")
    p.add_argument("--path", help="File path for Write/Edit/Read")
    p.add_argument("--bash-command", help="Bash command to analyze")
    p.add_argument("--agent", required=True, help="Agent ID")
    p.add_argument("--role", required=True, help="Role name")
    p.add_argument("--scope", default="", help="Agent scope")
    p.set_defaults(func=run)


def run(args):
    from governance.service import GovernanceService
    from integration.hooks import TOOL_ACTION_MAP

    service = GovernanceService(args.org_spec)
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
            params["command"] = args.bash_command
        elif args.path:
            params["path"] = args.path

        result = service.check_permission(args.agent, args.role, action, params)
        symbol = "APPROVE" if result.permitted else "BLOCK"
        print(f"  Tool:     {args.tool} -> {action}")
        if args.path:
            print(f"  Path:     {args.path}")
        print(f"  Decision: {symbol}")
        if result.reason:
            print(f"  Reason:   {result.reason}")

    if args.bash_command:
        from governance.bash_analyzer import analyze_bash_command

        print(f"\n  Bash command: {args.bash_command}")
        analysis = analyze_bash_command(args.bash_command)
        print(f"  Analysis:     {analysis.summary}")
        if analysis.writes:
            print(f"  Write paths:  {analysis.writes}")
            for write_path in analysis.writes:
                path_result = service.check_permission(
                    args.agent, args.role, "write_file", {"path": write_path},
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
