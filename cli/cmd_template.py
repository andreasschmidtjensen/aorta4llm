"""aorta template — manage governance templates (add, list)."""

from pathlib import Path

import yaml

from cli.cmd_init import TEMPLATES_DIR, list_templates
from cli.spec_utils import find_org_spec, load_spec, save_spec, rebuild_hooks


def add_parser(subparsers):
    p = subparsers.add_parser("template", help="Manage governance templates")
    template_sub = p.add_subparsers(dest="template_command")

    # aorta template list
    list_p = template_sub.add_parser("list", help="List available templates")
    list_p.set_defaults(func=run_list)

    # aorta template add
    add_p = template_sub.add_parser("add", help="Merge a template into an existing org spec")
    add_p.add_argument("template", help="Template name (e.g., test-gate)")
    add_p.add_argument("--org-spec", default=None)
    add_p.set_defaults(func=run_add)

    p.set_defaults(func=lambda args: _template_help(p, args))


def _template_help(parser, args):
    if not getattr(args, "template_command", None):
        parser.print_help()
        raise SystemExit(1)
    args.func(args)


def run_list(args):
    templates = list_templates()
    print("Available templates:")
    for t in templates:
        print(f"  {t['name']:20s} {t['description']}")
    print(f"  {'minimal':20s} Scope-only — no norms, no bash analysis")


def _norm_key(norm: dict) -> tuple:
    """Generate a dedup key for a norm."""
    ntype = norm.get("type", "")
    role = norm.get("role", "")
    if ntype in ("scope", "protected", "readonly"):
        return (ntype, role, tuple(sorted(norm.get("paths", []))))
    if ntype == "forbidden_command":
        return (ntype, role, norm.get("command_pattern", ""))
    if ntype == "required_before":
        return (ntype, role, norm.get("command_pattern", ""), norm.get("requires", ""))
    return (ntype, role, str(norm))


def run_add(args):
    template_path = TEMPLATES_DIR / f"{args.template}.yaml"
    if not template_path.exists():
        print(f"Template not found: {args.template}")
        print("Available:", ", ".join(t["name"] for t in list_templates()))
        raise SystemExit(1)

    spec_path = find_org_spec(args.org_spec)
    spec = load_spec(spec_path)

    with open(template_path) as f:
        template = yaml.safe_load(f)

    added_norms = 0
    added_triggers = 0
    existing_keys = {_norm_key(n) for n in spec.get("norms", [])}

    # Merge norms
    for norm in template.get("norms", []):
        key = _norm_key(norm)
        if key in existing_keys:
            ntype = norm.get("type", "?")
            print(f"  Skipped duplicate: {ntype} ({norm.get('role', '?')})")
            continue
        # Don't merge scope norms — they may have different paths
        if norm.get("type") == "scope":
            has_scope = any(n.get("type") == "scope" for n in spec.get("norms", []))
            if has_scope:
                print(f"  Skipped scope norm (existing scope preserved)")
                continue
        spec.setdefault("norms", []).append(norm)
        existing_keys.add(key)
        added_norms += 1

    # Merge achievement_triggers
    existing_marks = {t.get("marks") for t in spec.get("achievement_triggers", [])}
    for trigger in template.get("achievement_triggers", []):
        if trigger.get("marks") in existing_marks:
            print(f"  Skipped duplicate trigger: {trigger.get('marks')}")
            continue
        spec.setdefault("achievement_triggers", []).append(trigger)
        existing_marks.add(trigger.get("marks"))
        added_triggers += 1

    # Merge roles (union of objectives and capabilities)
    for role_name, role_def in template.get("roles", {}).items():
        existing_role = spec.setdefault("roles", {}).setdefault(role_name, {})
        existing_objs = set(existing_role.get("objectives", []))
        existing_caps = set(existing_role.get("capabilities", []))
        new_objs = set(role_def.get("objectives", []))
        new_caps = set(role_def.get("capabilities", []))
        existing_role["objectives"] = sorted(existing_objs | new_objs)
        existing_role["capabilities"] = sorted(existing_caps | new_caps)

    # Merge bash_analysis
    if template.get("bash_analysis"):
        spec["bash_analysis"] = True

    # Merge safe_commands
    if template.get("safe_commands"):
        existing_safe = set(spec.get("safe_commands", []))
        existing_safe.update(template["safe_commands"])
        spec["safe_commands"] = sorted(existing_safe)

    save_spec(spec_path, spec)
    rebuild_hooks(spec, spec_path)

    print(f"Merged template '{args.template}' into {spec_path}")
    print(f"  Norms added: {added_norms}")
    if added_triggers:
        print(f"  Triggers added: {added_triggers}")
