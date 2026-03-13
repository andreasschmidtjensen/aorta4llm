"""aorta protect / readonly / forbid / require — add norms from the CLI."""

from aorta4llm.cli.spec_utils import find_org_spec, load_spec, save_spec, rebuild_hooks


def add_protect_parser(subparsers):
    p = subparsers.add_parser("protect", help="Add a protected norm (blocks reads and writes)")
    p.add_argument("paths", nargs="+", help="Paths or globs to protect")
    p.add_argument("--org-spec", default=None)
    p.add_argument("--role", default="agent")
    p.set_defaults(func=run_protect)


def add_readonly_parser(subparsers):
    p = subparsers.add_parser("readonly", help="Add a readonly norm (blocks writes)")
    p.add_argument("paths", nargs="+", help="Paths or globs to make read-only")
    p.add_argument("--org-spec", default=None)
    p.add_argument("--role", default="agent")
    p.set_defaults(func=run_readonly)


def add_forbid_parser(subparsers):
    p = subparsers.add_parser("forbid", help="Add a forbidden_command norm")
    p.add_argument("pattern", help="Command pattern to forbid")
    p.add_argument("--severity", choices=["hard", "soft"], default="hard")
    p.add_argument("--org-spec", default=None)
    p.add_argument("--role", default="agent")
    p.set_defaults(func=run_forbid)


def add_require_parser(subparsers):
    p = subparsers.add_parser("require", help="Add a required_before norm")
    p.add_argument("achievement", help="Achievement name required")
    p.add_argument("--before", required=True, help="Command pattern to gate")
    p.add_argument("--org-spec", default=None)
    p.add_argument("--role", default="agent")
    p.set_defaults(func=run_require)


def _add_norm(norm: dict, org_spec_arg: str | None, notify: str) -> None:
    """Add a norm to the org spec and rebuild hooks."""
    spec_path = find_org_spec(org_spec_arg)
    spec = load_spec(spec_path)

    norms = spec.setdefault("norms", [])

    # Check for duplicate
    for existing in norms:
        if existing.get("type") == norm["type"] and existing.get("role") == norm.get("role"):
            if norm["type"] in ("protected", "readonly", "scope"):
                if existing.get("paths") == norm.get("paths"):
                    print(f"Norm already exists: {norm['type']} for {norm.get('paths')}")
                    return
            elif norm["type"] == "forbidden_command":
                if existing.get("command_pattern") == norm.get("command_pattern"):
                    print(f"Norm already exists: forbidden_command '{norm.get('command_pattern')}'")
                    return
            elif norm["type"] == "required_before":
                if existing.get("requires") == norm.get("requires") and existing.get("command_pattern") == norm.get("command_pattern"):
                    print(f"Norm already exists: required_before '{norm.get('command_pattern')}' requires {norm.get('requires')}")
                    return

    norms.append(norm)
    save_spec(spec_path, spec)
    rebuild_hooks(spec, spec_path)
    print(f"Added {notify} to {spec_path}")


def run_protect(args):
    spec_path = find_org_spec(args.org_spec)
    spec = load_spec(spec_path)
    access = spec.setdefault("access", {})
    for p in args.paths:
        access[p] = "no-access"
    save_spec(spec_path, spec)
    rebuild_hooks(spec, spec_path)
    print(f"Set no-access for {args.paths} in {spec_path}")


def run_readonly(args):
    spec_path = find_org_spec(args.org_spec)
    spec = load_spec(spec_path)
    access = spec.setdefault("access", {})
    for p in args.paths:
        access[p] = "read-only"
    save_spec(spec_path, spec)
    rebuild_hooks(spec, spec_path)
    print(f"Set read-only for {args.paths} in {spec_path}")


def run_forbid(args):
    norm: dict = {"type": "forbidden_command", "role": args.role, "command_pattern": args.pattern}
    if args.severity == "soft":
        norm["severity"] = "soft"
    label = f"forbidden_command '{args.pattern}'"
    if args.severity == "soft":
        label += " [soft]"
    _add_norm(norm, args.org_spec, label)


def run_require(args):
    norm = {
        "type": "required_before",
        "role": args.role,
        "command_pattern": args.before,
        "requires": args.achievement,
    }
    _add_norm(norm, args.org_spec, f"required_before: '{args.before}' requires {args.achievement}")
