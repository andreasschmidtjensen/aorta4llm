"""aorta include — add or remove policy packs."""

from pathlib import Path

import yaml

from aorta4llm.cli.cmd_init import TEMPLATES_DIR
from aorta4llm.cli.spec_utils import find_org_spec, load_spec, save_spec, rebuild_hooks

PACKS_DIR = TEMPLATES_DIR.parent / "packs"


def add_parser(subparsers):
    p = subparsers.add_parser("include", help="Add or remove policy packs")
    p.add_argument("pack", nargs="?", default=None, help="Pack name to add")
    p.add_argument("--remove", action="store_true", help="Remove the pack instead of adding")
    p.add_argument("--org-spec", default=None)
    p.set_defaults(func=run)


def list_packs() -> list[dict]:
    """List available packs with descriptions from header comments."""
    packs = []
    for f in sorted(PACKS_DIR.glob("*.yaml")):
        desc = ""
        with open(f) as fh:
            for line in fh:
                if line.startswith("#"):
                    desc = line.lstrip("# ").strip().rstrip(".")
                    break
                elif line.strip():
                    break
        packs.append({"name": f.stem, "description": desc})
    return packs


def run(args):
    if args.pack is None:
        packs = list_packs()
        if not packs:
            print("No packs available.")
            return
        print("Available packs:")
        for p in packs:
            print(f"  {p['name']:20s} {p['description']}")
        print(f"\nUsage: aorta include <pack>")
        return

    # Validate pack exists
    pack_path = PACKS_DIR / f"{args.pack}.yaml"
    if not pack_path.exists():
        print(f"Unknown pack: '{args.pack}'")
        print("Available:", ", ".join(p["name"] for p in list_packs()))
        raise SystemExit(1)

    spec_path = find_org_spec(args.org_spec)
    spec = load_spec(spec_path)
    includes = spec.get("include", [])

    if args.remove:
        if args.pack not in includes:
            print(f"Pack '{args.pack}' is not included in {spec_path}")
            return
        includes.remove(args.pack)
        if includes:
            spec["include"] = includes
        else:
            spec.pop("include", None)
        save_spec(spec_path, spec)
        rebuild_hooks(spec, spec_path)
        print(f"Removed pack '{args.pack}' from {spec_path}")
    else:
        if args.pack in includes:
            print(f"Pack '{args.pack}' is already included in {spec_path}")
            return
        includes.append(args.pack)
        spec["include"] = includes
        save_spec(spec_path, spec)
        rebuild_hooks(spec, spec_path)
        print(f"Added pack '{args.pack}' to {spec_path}")
