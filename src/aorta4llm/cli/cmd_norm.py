"""aorta remove-norm — remove a norm by index."""

from aorta4llm.cli.spec_utils import find_org_spec, load_spec, save_spec, rebuild_hooks


def add_parser(subparsers):
    p = subparsers.add_parser("remove-norm", help="Remove a norm by index (see 'aorta status')")
    p.add_argument("index", type=int, help="Norm index (1-based, from 'aorta status')")
    p.add_argument("--org-spec", default=None)
    p.set_defaults(func=run)


def run(args):
    spec_path = find_org_spec(args.org_spec)
    spec = load_spec(spec_path)
    norms = spec.get("norms", [])

    idx = args.index - 1  # 1-based to 0-based
    if idx < 0 or idx >= len(norms):
        print(f"Invalid index {args.index}. Spec has {len(norms)} norm(s).")
        raise SystemExit(1)

    removed = norms.pop(idx)
    save_spec(spec_path, spec)
    rebuild_hooks(spec, spec_path)

    ntype = removed.get("type", "?")
    detail = ""
    if ntype in ("scope", "protected", "readonly"):
        detail = f" — {', '.join(removed.get('paths', []))}"
    elif ntype == "forbidden_command":
        detail = f" — '{removed.get('command_pattern', '')}'"
    elif ntype == "required_before":
        detail = f" — '{removed.get('command_pattern', '')}' requires {removed.get('requires', '?')}"

    print(f"Removed norm #{args.index}: {ntype}{detail}")
    print(f"Updated {spec_path}")
