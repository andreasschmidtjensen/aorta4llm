"""aorta init — interactive scaffolding for governance setup."""

import json
import shutil
from pathlib import Path

import yaml

TEMPLATES_DIR = Path(__file__).parent.parent / "org-specs" / "templates"


def add_parser(subparsers):
    p = subparsers.add_parser("init", help="Initialize governance for this project")
    p.add_argument("--template", help="Template name (safe-agent, test-gate, review-gate)")
    p.add_argument("--scope", default="src/", help="Directory scope for the agent")
    p.add_argument("--agent", default="dev", help="Agent name")
    p.add_argument("--list-templates", action="store_true", help="List available templates")
    p.set_defaults(func=run)


def list_templates() -> list[dict]:
    """List available templates with descriptions from their header comments."""
    templates = []
    for f in sorted(TEMPLATES_DIR.glob("*.yaml")):
        desc = ""
        with open(f) as fh:
            for line in fh:
                if line.startswith("#"):
                    desc = line.lstrip("# ").strip().rstrip(".")
                    break
                elif line.strip():
                    break
        templates.append({"name": f.stem, "path": f, "description": desc})
    return templates


def run(args):
    if args.list_templates:
        for t in list_templates():
            print(f"  {t['name']:20s} {t['description']}")
        return

    if not args.template:
        print("Available templates:")
        for t in list_templates():
            print(f"  {t['name']:20s} {t['description']}")
        print("\nUsage: aorta init --template <name> --scope <dir>")
        raise SystemExit(1)

    template_path = TEMPLATES_DIR / f"{args.template}.yaml"
    if not template_path.exists():
        print(f"Template not found: {args.template}")
        print("Available:", ", ".join(t["name"] for t in list_templates()))
        raise SystemExit(1)

    # 1. Copy template to .aorta/
    org_spec_dest = Path(f".aorta/{args.template}.yaml")
    org_spec_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_path, org_spec_dest)
    print(f"Copied template to {org_spec_dest}")

    # 2. Determine role from template.
    with open(org_spec_dest) as f:
        spec = yaml.safe_load(f)
    roles = list(spec.get("roles", {}).keys())
    role = roles[0] if roles else "agent"
    for r in roles:
        if r not in ("reviewer",):
            role = r
            break

    # 3. Build hooks config.
    spec_rel = str(org_spec_dest)
    needs_post = bool(spec.get("achievement_triggers"))

    hooks_config: dict = {
        "PreToolUse": [{
            "matcher": "Write|Edit|NotebookEdit|Bash",
            "command": f"uv run python -m integration.hooks pre-tool-use --org-spec {spec_rel} --agent {args.agent}",
        }],
    }
    if needs_post:
        hooks_config["PostToolUse"] = [{
            "matcher": "Bash",
            "command": f"uv run python -m integration.hooks post-tool-use --org-spec {spec_rel} --agent {args.agent}",
        }]

    # 4. Write .claude/settings.local.json (merge if exists).
    settings_path = Path(".claude/settings.local.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if settings_path.exists():
        existing = json.loads(settings_path.read_text())
    existing.setdefault("hooks", {}).update(hooks_config)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"Wrote hooks config to {settings_path}")

    # 5. Register the agent.
    from integration.hooks import GovernanceHook
    hook = GovernanceHook(org_spec_dest)
    hook.register_agent(args.agent, role, args.scope)
    print(f"Registered agent '{args.agent}' as '{role}' with scope '{args.scope}'")

    # 6. Summary.
    print(f"\nSetup complete:")
    print(f"  Org spec:  {org_spec_dest}")
    print(f"  Hooks:     {settings_path}")
    print(f"  Agent:     {args.agent} (role: {role}, scope: {args.scope})")
    if needs_post:
        print(f"  PostToolUse hooks enabled (achievement triggers detected)")
    print(f"\nRun 'aorta validate {org_spec_dest}' to verify the spec.")
