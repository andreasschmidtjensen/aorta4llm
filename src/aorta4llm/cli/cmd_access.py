"""aorta access — set per-directory access levels."""

from aorta4llm.cli.spec_utils import find_org_spec, load_spec, save_spec, rebuild_hooks


VALID_LEVELS = ("read-write", "read-only", "no-access")


def add_parser(subparsers):
    p = subparsers.add_parser("access", help="Set access level for a path")
    p.add_argument("path", help="Path or glob pattern")
    p.add_argument("level", choices=VALID_LEVELS, help="Access level")
    p.add_argument("--org-spec", default=None)
    p.set_defaults(func=run)


def run(args):
    spec_path = find_org_spec(args.org_spec)
    spec = load_spec(spec_path)

    access = spec.setdefault("access", {})
    old_level = access.get(args.path)
    access[args.path] = args.level

    save_spec(spec_path, spec)
    rebuild_hooks(spec, spec_path)

    if old_level:
        print(f"Changed '{args.path}': {old_level} -> {args.level}")
    else:
        print(f"Set '{args.path}': {args.level}")
    print(f"Updated {spec_path}")
