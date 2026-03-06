"""aorta init — interactive scaffolding for governance setup."""

import json
import re
import shutil
from pathlib import Path

import yaml

TEMPLATES_DIR = Path(__file__).parent.parent / "org-specs" / "templates"


def add_parser(subparsers):
    p = subparsers.add_parser("init", help="Initialize governance for this project")
    p.add_argument("--template", help="Template name (safe-agent, test-gate, review-gate)")
    p.add_argument("--scope", nargs="+", default=["src/"], help="Directory scope(s) for the agent (e.g. --scope src/ tests/)")
    p.add_argument("--agent", default="dev", help="Agent name")
    p.add_argument("--list-templates", action="store_true", help="List available templates")
    p.add_argument("--strict", action="store_true", help="Also hook Read/Glob/Grep to enforce read restrictions")
    p.add_argument("--with-dashboard", action="store_true", help="Print command to launch the governance dashboard")
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

    # Normalize scopes: ensure trailing slash
    scopes = [s.rstrip("/") + "/" for s in args.scope]
    scope_str = " ".join(scopes)  # for display and registration

    # 1. Read template, strip header comments, update for user's config.
    with open(template_path) as f:
        spec = yaml.safe_load(f)

    org_spec_dest = Path(f".aorta/{args.template}.yaml")
    org_spec_dest.parent.mkdir(parents=True, exist_ok=True)

    # 2. Update scope norms to match --scope.
    new_norms = []
    for norm in spec.get("norms", []):
        if norm.get("type") == "scope":
            norm["paths"] = scopes
            new_norms.append(norm)
        else:
            new_norms.append(norm)
    spec["norms"] = new_norms

    with open(org_spec_dest, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)

    print(f"Created org spec at {org_spec_dest}")
    print(f"  Allowed scope(s): {scope_str}")

    # 3. Determine role from template.
    roles = list(spec.get("roles", {}).keys())
    role = roles[0] if roles else "agent"
    for r in roles:
        if r not in ("reviewer",):
            role = r
            break

    # 4. Build hooks config (Claude Code current format).
    spec_rel = str(org_spec_dest)
    events_rel = ".aorta/events.jsonl"
    needs_post = bool(spec.get("achievement_triggers"))

    pre_cmd = f"aorta hook pre-tool-use --org-spec {spec_rel} --agent {args.agent} --events-path {events_rel}"
    post_cmd = f"aorta hook post-tool-use --org-spec {spec_rel} --agent {args.agent} --events-path {events_rel}"

    # Write tools matcher — add Read/Glob/Grep if --strict or protected norms exist
    has_protected = any(n.get("type") == "protected" for n in spec.get("norms", []))
    write_matcher = "Write|Edit|NotebookEdit|Bash"
    if args.strict or has_protected:
        write_matcher = "Write|Edit|NotebookEdit|Bash|Read|Glob|Grep"

    hooks_config: dict = {
        "PreToolUse": [{
            "matcher": write_matcher,
            "hooks": [{"type": "command", "command": pre_cmd}],
        }],
    }
    if needs_post:
        hooks_config["PostToolUse"] = [{
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": post_cmd}],
        }]

    # 5. Write .claude/settings.local.json — fully replace hooks section.
    settings_path = Path(".claude/settings.local.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if settings_path.exists():
        existing = json.loads(settings_path.read_text())
    existing["hooks"] = hooks_config  # replace, not merge
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"Wrote hooks config to {settings_path}")
    if args.strict:
        print(f"  Strict mode: Read/Glob/Grep also hooked")

    # 6. Register the agent.
    from integration.hooks import GovernanceHook
    hook = GovernanceHook(org_spec_dest, events_path=events_rel)
    hook.register_agent(args.agent, role, scope_str)
    print(f"Registered agent '{args.agent}' as '{role}' with scope '{scope_str}'")

    # 7. Summary.
    print(f"\nSetup complete:")
    print(f"  Org spec:  {org_spec_dest}")
    print(f"  Hooks:     {settings_path}")
    print(f"  Events:    {events_rel}")
    print(f"  Agent:     {args.agent} (role: {role}, scope: {scope_str})")
    if needs_post:
        print(f"  PostToolUse hooks enabled (achievement triggers detected)")
    print(f"\nRun 'aorta validate {org_spec_dest}' to verify the spec.")

    if args.with_dashboard:
        print(f"\nTo launch the dashboard:")
        print(f"  uv run --extra dashboard python -m dashboard.server --org-spec {org_spec_dest} --events {events_rel}")
