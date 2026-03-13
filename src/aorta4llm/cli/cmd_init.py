"""aorta init — interactive scaffolding for governance setup."""

import json
import re
import shutil
from pathlib import Path

import yaml

_WHEEL_TEMPLATES = Path(__file__).parent / "org-specs" / "templates"
_DEV_TEMPLATES = Path(__file__).parent.parent / "org-specs" / "templates"
TEMPLATES_DIR = _WHEEL_TEMPLATES if _WHEEL_TEMPLATES.is_dir() else _DEV_TEMPLATES


def _is_aorta_hook(hook_entry: dict) -> bool:
    """Check if a hook entry belongs to aorta."""
    for h in hook_entry.get("hooks", []):
        if "aorta hook" in h.get("command", ""):
            return True
    return False


def _has_aorta_hooks(hooks_config: dict) -> bool:
    """Check if any event type has aorta hooks."""
    for event_type, entries in hooks_config.items():
        if isinstance(entries, list):
            for entry in entries:
                if _is_aorta_hook(entry):
                    return True
    return False


def _merge_hooks(old_hooks: dict, new_aorta_hooks: dict) -> dict:
    """Merge hooks: keep non-aorta hooks, replace aorta hooks with new ones.

    For each event type (PreToolUse, PostToolUse, etc.):
    - Keep any existing entries that are NOT aorta hooks
    - Add the new aorta entries from new_aorta_hooks
    - Remove event types that had only aorta hooks and aren't in new config
    """
    merged: dict = {}

    # Collect all event types from both old and new.
    all_events = set(old_hooks.keys()) | set(new_aorta_hooks.keys())

    for event_type in all_events:
        entries = []

        # Keep non-aorta hooks from old config.
        for entry in old_hooks.get(event_type, []):
            if not _is_aorta_hook(entry):
                entries.append(entry)

        # Add new aorta hooks.
        entries.extend(new_aorta_hooks.get(event_type, []))

        if entries:
            merged[event_type] = entries

    return merged


def add_parser(subparsers):
    p = subparsers.add_parser("init", help="Initialize governance for this project")
    p.add_argument("--template", help="Template name (safe-agent, test-gate, or minimal)")
    p.add_argument("--scope", nargs="+", default=["src/"], help="Directory scope(s) for the agent (e.g. --scope src/ tests/)")
    p.add_argument("--list-templates", action="store_true", help="List available templates")
    p.add_argument("--strict", action="store_true", help="Also hook Read/Glob/Grep to enforce read restrictions")
    p.add_argument("--reinit", action="store_true", help="Overwrite existing aorta hooks without prompting")
    p.add_argument("--dry-run", action="store_true", help="Show what would be created without creating anything")
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
        print(f"  {'minimal':20s} Scope-only — no norms, no bash analysis")
        print("\nUsage: aorta init --template <name> --scope <dir>")
        raise SystemExit(1)

    # Normalize scopes: ensure trailing slash.
    # Handle both "--scope src/ tests/" (multiple args) and "--scope 'src/ tests/'" (single arg with spaces).
    raw_scopes = []
    for s in args.scope:
        raw_scopes.extend(s.split())
    scopes = [s.rstrip("/") + "/" for s in raw_scopes]
    scope_str = " ".join(scopes)  # for display and registration

    # Handle --template minimal as a built-in (no YAML file needed).
    if args.template == "minimal":
        spec = {
            "organization": "minimal",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "access": {s: "read-write" for s in scopes},
        }
        org_spec_dest = Path(".aorta/minimal.yaml")
    else:
        template_path = TEMPLATES_DIR / f"{args.template}.yaml"
        if not template_path.exists():
            print(f"Template not found: {args.template}")
            print("Available:", ", ".join(t["name"] for t in list_templates()))
            raise SystemExit(1)

        # 1. Read template, strip header comments, update for user's config.
        with open(template_path) as f:
            spec = yaml.safe_load(f)

        org_spec_dest = Path(f".aorta/{args.template}.yaml")

    # 2. Update scope to match --scope (skip for minimal — already set).
    if args.template != "minimal":
        # If the template has an access map, update read-write entries.
        # Otherwise, update scope norms (legacy templates).
        if "access" in spec:
            access = spec["access"]
            # Remove existing read-write entries, add new ones from --scope.
            old_rw = [k for k, v in access.items() if v == "read-write"]
            for k in old_rw:
                del access[k]
            # Insert new read-write scopes at the beginning.
            new_access = {s: "read-write" for s in scopes}
            new_access.update(access)
            spec["access"] = new_access
        else:
            new_norms = []
            for norm in spec.get("norms", []):
                if norm.get("type") == "scope":
                    norm["paths"] = scopes
                    new_norms.append(norm)
                else:
                    new_norms.append(norm)
            spec["norms"] = new_norms

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

    agent_name = "agent"
    pre_cmd = f"aorta hook pre-tool-use --org-spec {spec_rel} --agent {agent_name} --events-path {events_rel}"
    post_cmd = f"aorta hook post-tool-use --org-spec {spec_rel} --agent {agent_name} --events-path {events_rel}"

    # Write tools matcher — add Read/Glob/Grep if --strict or protected/no-access entries exist
    has_protected = any(n.get("type") == "protected" for n in spec.get("norms", []))
    has_no_access = any(v == "no-access" for v in spec.get("access", {}).values())
    write_matcher = "Write|Edit|NotebookEdit|Bash"
    if args.strict or has_protected or has_no_access:
        write_matcher = "Write|Edit|NotebookEdit|Bash|Read|Glob|Grep"

    # Sensitive paths: read-only or no-access entries need PostToolUse for content warnings.
    has_sensitive = any(v in ("read-only", "no-access") for v in spec.get("access", {}).values())

    hooks_config: dict = {
        "PreToolUse": [{
            "matcher": write_matcher,
            "hooks": [{"type": "command", "command": pre_cmd}],
        }],
    }
    post_matchers = []
    if needs_post:
        post_matchers.append("Bash")
    if has_sensitive:
        post_matchers.extend(["Read", "Glob", "Grep"])
    if post_matchers:
        hooks_config["PostToolUse"] = [{
            "matcher": "|".join(sorted(set(post_matchers))),
            "hooks": [{"type": "command", "command": post_cmd}],
        }]

    # --- Dry run: show what would be created and exit ---
    if args.dry_run:
        print("Dry run — no files will be created.\n")
        print("Would create:")
        print(f"  {org_spec_dest}")
        print(f"  .claude/settings.local.json")
        print(f"  .aorta/state.json (agent 'agent' registered)")
        print()

        # Show org spec contents summary.
        access = spec.get("access", {})
        if access:
            print("Access map:")
            for path, level in access.items():
                print(f"  {path:20s} {level}")
            print()

        norms = spec.get("norms", [])
        if norms:
            print(f"Norms ({len(norms)}):")
            for norm in norms:
                ntype = norm.get("type", "?")
                severity = f" [{norm['severity']}]" if norm.get("severity") else ""
                if ntype == "forbidden_command":
                    print(f"  {ntype}{severity}: {norm.get('command_pattern', '')}")
                elif ntype == "required_before":
                    print(f"  {ntype}: '{norm.get('command_pattern', '')}' requires {norm.get('requires', '?')}")
                else:
                    print(f"  {ntype}{severity}")
            print()

        print(f"Hooks:")
        print(f"  PreToolUse: {write_matcher}")
        if needs_post:
            print(f"  PostToolUse: Bash")
        return

    # --- Actual init ---

    org_spec_dest.parent.mkdir(parents=True, exist_ok=True)

    with open(org_spec_dest, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)

    print(f"Created org spec at {org_spec_dest}")
    print(f"  Allowed scope(s): {scope_str}")

    # 5. Write .claude/settings.local.json — smart merge with existing hooks.
    settings_path = Path(".claude/settings.local.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if settings_path.exists():
        existing = json.loads(settings_path.read_text())

    old_hooks = existing.get("hooks", {})
    has_aorta_hooks = _has_aorta_hooks(old_hooks)

    if has_aorta_hooks and not args.reinit:
        print("Aorta hooks already configured in .claude/settings.local.json.")
        print("Use --reinit to overwrite them.")
        raise SystemExit(1)

    # Merge: keep non-aorta hooks, replace aorta hooks with new ones.
    merged_hooks = _merge_hooks(old_hooks, hooks_config)
    existing["hooks"] = merged_hooks
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"Wrote hooks config to {settings_path}")
    if has_protected or has_no_access or args.strict:
        print(f"  Read/Glob/Grep hooked (no-access entries detected)")

    # 6. Register the agent.
    from aorta4llm.integration.hooks import GovernanceHook
    hook = GovernanceHook(org_spec_dest, events_path=events_rel)
    if args.reinit:
        hook.clear_transient_state()
    hook.register_agent("agent", role, scope_str, reinit=args.reinit)
    print(f"Registered agent '{agent_name}' as '{role}' with scope '{scope_str}'")

    # 7. Create slash commands for agent introspection.
    commands_dir = Path(".claude/commands/aorta")
    commands_dir.mkdir(parents=True, exist_ok=True)
    (commands_dir / "permissions.md").write_text(
        f"Run `aorta permissions --org-spec {spec_rel}` and show the output.\n"
    )
    (commands_dir / "status.md").write_text(
        f"Run `aorta status --org-spec {spec_rel}` and show the output.\n"
    )
    print(f"Created slash commands in {commands_dir}")

    # 8. Summary.
    print(f"\nSetup complete:")
    print(f"  Org spec:  {org_spec_dest}")
    print(f"  Hooks:     {settings_path}")
    print(f"  Events:    {events_rel}")
    print(f"  Agent:     {agent_name} (role: {role}, scope: {scope_str})")
    print(f"  Commands:  /aorta:permissions, /aorta:status")
    if needs_post:
        print(f"  PostToolUse hooks enabled (achievement triggers detected)")

    safe_cmds = spec.get("safe_commands", [])
    if spec.get("bash_analysis") and safe_cmds:
        print(f"\nBash analysis enabled. Commands that skip analysis (fast path):")
        print(f"  {', '.join(safe_cmds)}")
        print(f"  Add custom commands (jest, cargo test, etc.) to safe_commands in {org_spec_dest}")

    print(f"\nRun 'aorta validate {org_spec_dest}' to verify the spec.")
    print(f"Run 'aorta watch' in a separate terminal to monitor governance events in real-time.")

