"""Shared utilities for CLI commands that modify org specs."""

import json
from pathlib import Path

import yaml


def find_org_spec(explicit: str | None = None) -> Path:
    """Find the org spec to use: explicit path, or first .yaml in .aorta/."""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"Org spec not found: {p}")
        return p
    aorta_dir = Path(".aorta")
    if not aorta_dir.is_dir():
        raise SystemExit("No .aorta/ directory found. Run: aorta init --template safe-agent --scope src/")
    specs = sorted(aorta_dir.glob("*.yaml"))
    if not specs:
        raise SystemExit("No org spec YAML in .aorta/. Run: aorta init --template safe-agent --scope src/")
    if len(specs) > 1:
        names = ", ".join(str(s) for s in specs)
        raise SystemExit(f"Multiple org specs found ({names}). Use --org-spec to pick one.")
    return specs[0]


def load_spec(path: Path) -> dict:
    """Load an org spec YAML file."""
    with open(path) as f:
        return yaml.safe_load(f)


def save_spec(path: Path, spec: dict) -> None:
    """Write an org spec YAML file."""
    with open(path, "w") as f:
        yaml.dump(spec, f, default_flow_style=False, sort_keys=False)


def rebuild_hooks(spec: dict, spec_path: Path) -> None:
    """Rebuild .claude/settings.local.json hooks based on the org spec.

    Preserves non-aorta hooks, replaces aorta hooks with fresh config.
    """
    from aorta4llm.cli.cmd_init import _merge_hooks, _is_aorta_hook

    spec_rel = str(spec_path)
    events_rel = ".aorta/events.jsonl"
    needs_post = bool(spec.get("achievement_triggers"))
    has_protected = any(n.get("type") == "protected" for n in spec.get("norms", []))
    # Access map with no-access entries also implies protected norms
    has_protected = has_protected or any(
        v == "no-access" for v in spec.get("access", {}).values()
    )

    pre_cmd = f"aorta hook pre-tool-use --org-spec {spec_rel} --agent agent --events-path {events_rel}"
    post_cmd = f"aorta hook post-tool-use --org-spec {spec_rel} --agent agent --events-path {events_rel}"
    session_cmd = f"aorta hook session-start --org-spec {spec_rel} --agent agent --events-path {events_rel}"

    write_matcher = "Write|Edit|NotebookEdit|Bash"
    if has_protected:
        write_matcher = "Write|Edit|NotebookEdit|Bash|Read|Glob|Grep"

    has_sensitive = any(v in ("read-only", "no-access") for v in spec.get("access", {}).values())

    hooks_config: dict = {
        "SessionStart": [{
            "hooks": [{"type": "command", "command": session_cmd}],
        }],
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

    settings_path = Path(".claude/settings.local.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if settings_path.exists():
        existing = json.loads(settings_path.read_text())

    old_hooks = existing.get("hooks", {})
    existing["hooks"] = _merge_hooks(old_hooks, hooks_config)
    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
