"""aorta doctor — diagnose setup issues."""

import json
import shutil
from pathlib import Path

import yaml


def add_parser(subparsers):
    p = subparsers.add_parser("doctor", help="Diagnose aorta setup issues")
    p.set_defaults(func=run)


def _find_aorta_dir() -> Path | None:
    """Walk up from cwd to find .aorta/ directory."""
    current = Path.cwd().resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".aorta"
        if candidate.is_dir():
            return candidate
    return None


def _check_mark(ok: bool, message: str) -> bool:
    prefix = "\u2713" if ok else "\u2717"
    print(f"{prefix} {message}")
    return ok


def run(args):
    issues = 0

    # 1. Find .aorta/ directory
    aorta_dir = _find_aorta_dir()
    if not _check_mark(aorta_dir is not None, ".aorta/ found" if aorta_dir else ".aorta/ not found"):
        print("  Run: aorta init --template safe-agent --scope src/")
        issues += 1
        print(f"\n{issues} issue(s) found.")
        return

    project_root = aorta_dir.parent

    # 2. Find and validate org specs
    specs = sorted(aorta_dir.glob("*.yaml"))
    if not specs:
        _check_mark(False, "No org spec YAML files in .aorta/")
        print("  Run: aorta init --template safe-agent --scope src/")
        issues += 1
    else:
        from aorta4llm.governance.validator import validate_spec_file
        for spec_path in specs:
            result = validate_spec_file(spec_path)
            rel = spec_path.relative_to(project_root)
            if result.valid:
                _check_mark(True, f"Org spec: {rel} (valid)")
            else:
                _check_mark(False, f"Org spec: {rel} (invalid)")
                for err in result.errors:
                    print(f"    {err}")
                issues += 1

    # 3. Check .claude/settings.local.json
    settings_path = project_root / ".claude" / "settings.local.json"
    if not settings_path.exists():
        _check_mark(False, "Hooks config: .claude/settings.local.json not found")
        print("  Run: aorta init --template safe-agent --scope src/")
        issues += 1
    else:
        _check_mark(True, f"Hooks config: .claude/settings.local.json")
        try:
            settings = json.loads(settings_path.read_text())
            hooks = settings.get("hooks", {})
            for event_type, entries in hooks.items():
                if isinstance(entries, list):
                    for entry in entries:
                        for h in entry.get("hooks", []):
                            if "aorta hook" in h.get("command", ""):
                                matcher = entry.get("matcher", "*")
                                print(f"  {event_type}: {matcher}")
        except (json.JSONDecodeError, KeyError):
            _check_mark(False, "  Could not parse settings file")
            issues += 1

    # 4. Check hook binary is executable
    aorta_bin = shutil.which("aorta")
    if aorta_bin:
        _check_mark(True, f"Hook binary: {aorta_bin}")
    else:
        _check_mark(False, "Hook binary: 'aorta' not found on PATH")
        print("  Install with: pip install aorta4llm  (or uv pip install aorta4llm)")
        issues += 1

    # 5. Check state file
    state_path = aorta_dir / "state.json"
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            agents = [e["agent"] for e in state.get("events", []) if e.get("type") == "register"]
            unique_agents = list(dict.fromkeys(agents))  # dedupe, preserve order
            if unique_agents:
                agent_list = ", ".join(unique_agents)
                _check_mark(True, f"State: {len(unique_agents)} agent(s) registered ({agent_list})")
            else:
                _check_mark(False, "State: no agents registered")
                print("  Run: aorta init --template safe-agent --scope src/")
                issues += 1
        except (json.JSONDecodeError, KeyError):
            _check_mark(False, "State: invalid JSON in .aorta/state.json")
            issues += 1
    else:
        _check_mark(False, "State: .aorta/state.json not found")
        print("  Run: aorta init --template safe-agent --scope src/")
        issues += 1

    # 6. Quick dry-run
    if specs and not issues:
        try:
            from aorta4llm.governance.compiler import compile_org_spec
            from aorta4llm.governance.py_engine import PythonGovernanceEngine
            spec_path = specs[0]
            compiled = compile_org_spec(spec_path)
            engine = PythonGovernanceEngine()
            engine.load_org_spec(compiled)
            _check_mark(True, "Engine: dry-run passed")
        except Exception as e:
            _check_mark(False, f"Engine: dry-run failed ({e})")
            issues += 1

    print()
    if issues == 0:
        print("All checks passed.")
    else:
        print(f"{issues} issue(s) found.")
