"""YAML -> Prolog fact compiler for organizational specifications."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CompiledSpec:
    """Holds compiled Prolog facts and rules from an org spec."""

    facts: list[str] = field(default_factory=list)   # No trailing '.'
    rules: list[str] = field(default_factory=list)    # With trailing '.'


def compile_org_spec(yaml_path: str | Path) -> CompiledSpec:
    """Compile a YAML org spec file to Prolog facts and rules."""
    path = Path(yaml_path)
    with open(path) as f:
        spec_dict = yaml.safe_load(f)
    return compile_spec_dict(spec_dict)


def compile_spec_dict(spec_dict: dict) -> CompiledSpec:
    """Compile an org spec dictionary to Prolog facts and rules.

    This is the core logic, testable without files.
    """
    spec = CompiledSpec()

    _compile_roles(spec_dict.get("roles", {}), spec)
    _compile_dependencies(spec_dict.get("dependencies", []), spec)

    # Expand access map into norms before compiling
    norms = list(spec_dict.get("norms", []))
    norms.extend(_expand_access_map(spec_dict.get("access", {})))
    _compile_norms(norms, spec)

    _compile_rules(spec_dict.get("rules", []), spec)

    return spec


def _compile_roles(roles: dict, spec: CompiledSpec) -> None:
    """Compile role definitions to role/2, cap/2, and obj/2 facts."""
    for role_name, role_def in roles.items():
        objectives = role_def.get("objectives", [])
        obj_list = _to_prolog_list(objectives)
        spec.facts.append(f"role({role_name}, {obj_list})")

        for cap in role_def.get("capabilities", []):
            spec.facts.append(f"cap({role_name}, {cap})")

        for obj in objectives:
            sub_objs = "[]"  # No sub-objectives in Phase 1
            spec.facts.append(f"obj({obj}, {sub_objs})")


def _compile_dependencies(deps: list, spec: CompiledSpec) -> None:
    """Compile dependency relations to dep/3 facts."""
    for dep in deps:
        role = dep["role"]
        depends_on = dep["depends_on"]
        for_obj = dep["for"]
        spec.facts.append(f"dep({role}, {depends_on}, {for_obj})")


def _compile_norms(norms: list, spec: CompiledSpec) -> None:
    """Compile conditional norms to cond/5 facts.

    Handles both raw Prolog-syntax norms and high-level shorthand types:
    - scope: forbid writes outside a given directory prefix
    - readonly: forbid writes matching any of a list of path prefixes
    - required_before: block a command until an achievement exists
    """
    for norm in norms:
        norm_type = norm["type"]

        if norm_type == "scope":
            _compile_scope(norm, spec)
        elif norm_type == "readonly":
            _compile_readonly(norm, spec)
        elif norm_type == "required_before":
            _compile_required_before(norm, spec)
        elif norm_type == "protected":
            _compile_protected(norm, spec)
        elif norm_type == "forbidden_command":
            _compile_forbidden_command(norm, spec)
        else:
            # Raw syntax: obliged / forbidden with explicit objective + condition
            role = norm["role"]
            deon = norm_type  # 'obliged' or 'forbidden'
            objective = norm["objective"]
            deadline = norm.get("deadline", "false")
            condition = norm.get("condition", "true")
            spec.facts.append(
                f"cond({role}, {deon}, {objective}, {deadline}, {condition})"
            )

        # Any norm type can have severity: soft
        if norm.get("severity") == "soft" and norm_type not in ("forbidden_command",):
            role = norm.get("role", "")
            objective = norm.get("objective", "")
            if role and objective:
                spec.facts.append(f"soft_norm({role}, {objective})")


def _compile_scope(norm: dict, spec: CompiledSpec) -> None:
    """Compile scope shorthand.

    Forbids write_file(Path) for any Path not inside one of the allowed scopes.
    """
    role = norm["role"]
    scope_paths = [p.rstrip("/") + "/" for p in norm["paths"]]

    if len(scope_paths) == 1:
        quoted = f"'{scope_paths[0]}'"
        spec.facts.append(
            f"cond({role}, forbidden, write_file(Path), false, "
            f"not(in_scope(Path, {quoted})))"
        )
    else:
        # Multi-scope: block if not in ANY of the allowed scopes
        # not(in_any_scope(Path, ['src/', 'tests/']))
        scope_list = "[" + ", ".join(f"'{p}'" for p in scope_paths) + "]"
        spec.facts.append(
            f"cond({role}, forbidden, write_file(Path), false, "
            f"not(in_any_scope(Path, {scope_list})))"
        )
        _ensure_in_any_scope_rule(spec)

    _ensure_in_scope_rule(spec)


def _is_glob_pattern(path: str) -> bool:
    """Check if a path contains glob metacharacters."""
    return any(c in path for c in "*?[")


def _compile_readonly(norm: dict, spec: CompiledSpec) -> None:
    """Compile readonly shorthand.

    Forbids write_file(Path) for any Path that starts with one of the listed prefixes.
    Supports glob patterns (e.g., '*.key', '**/*.secret') via path_matches/2.
    """
    role = norm["role"]
    paths = norm.get("paths", [])
    for p in paths:
        if _is_glob_pattern(p):
            condition = f"path_matches(Path, '{p}')"
        else:
            prefix = p.rstrip("/")
            quoted = f"'{prefix}'"
            condition = f"atom_concat({quoted}, _, Path)"
        spec.facts.append(
            f"cond({role}, forbidden, write_file(Path), false, {condition})"
        )


def _compile_protected(norm: dict, spec: CompiledSpec) -> None:
    """Compile protected shorthand.

    Forbids both read_file(Path) and write_file(Path) for paths matching
    any of the listed prefixes. Supports glob patterns via path_matches/2.
    """
    role = norm["role"]
    for p in norm["paths"]:
        if _is_glob_pattern(p):
            condition = f"path_matches(Path, '{p}')"
        else:
            prefix = p.rstrip("/")
            quoted = f"'{prefix}'"
            condition = f"atom_concat({quoted}, _, Path)"
        spec.facts.append(
            f"cond({role}, forbidden, read_file(Path), false, {condition})"
        )
        spec.facts.append(
            f"cond({role}, forbidden, write_file(Path), false, {condition})"
        )


def _compile_required_before(norm: dict, spec: CompiledSpec) -> None:
    """Compile required_before shorthand.

    Blocks an execute_command(Cmd) unless a given achievement exists.
    Uses a helper rule named after a hash of the command_pattern to avoid collisions.
    """
    role = norm["role"]
    command_pattern = norm.get("command_pattern", "")
    requires = norm["requires"]

    # Generate a stable short name from the pattern
    h = hashlib.sha1(command_pattern.encode()).hexdigest()[:6]
    helper = f"cmd_matches_{h}"

    # cond/5: block execute_command(Cmd) when helper matches but achievement missing
    # Use parenthesized conjunction (A, B) which the terms parser understands
    condition = f"({helper}(Cmd), not(achieved({requires})))"
    spec.facts.append(
        f"cond({role}, forbidden, execute_command(Cmd), false, {condition})"
    )

    # Helper rule: matches when command contains the pattern as a substring
    quoted = f"'{command_pattern}'"
    spec.rules.append(
        f"{helper}(Cmd) :- str_contains(Cmd, {quoted})."
    )


def _compile_forbidden_command(norm: dict, spec: CompiledSpec) -> None:
    """Compile forbidden_command shorthand.

    Forbids execute_command(Cmd) when the command contains `command_pattern`
    as a substring. Uses str_contains/2 for matching.
    Supports severity: soft for confirmation-required blocks.
    """
    role = norm["role"]
    command_pattern = norm["command_pattern"]
    quoted = f"'{command_pattern}'"

    spec.facts.append(
        f"cond({role}, forbidden, execute_command(Cmd), false, str_contains(Cmd, {quoted}))"
    )

    if norm.get("severity") == "soft":
        spec.facts.append(f"soft_norm({role}, execute_command(Cmd), str_contains(Cmd, {quoted}))")


def _ensure_in_scope_rule(spec: CompiledSpec) -> None:
    """Add the standard in_scope/2 rule if not already present."""
    rule = "in_scope(Path, Scope) :- atom_concat(Scope, _, Path)."
    if rule not in spec.rules:
        spec.rules.append(rule)


def _ensure_in_any_scope_rule(spec: CompiledSpec) -> None:
    """Add in_any_scope/2 rule: true if Path starts with any scope in the list."""
    rule = "in_any_scope(Path, Scopes) :- member(Scope, Scopes), in_scope(Path, Scope)."
    if rule not in spec.rules:
        spec.rules.append(rule)
    _ensure_in_scope_rule(spec)


def _compile_rules(rules: list, spec: CompiledSpec) -> None:
    """Pass through Prolog rules verbatim."""
    for rule in rules:
        rule = rule.strip()
        if not rule.endswith("."):
            rule += "."
        spec.rules.append(rule)


def _expand_access_map(access: dict, role: str = "agent") -> list[dict]:
    """Expand an access map into norm dicts.

    access:
      src/:       read-write
      config/:    read-only
      .env:       no-access

    Generates scope, readonly, and protected norms.
    """
    if not access:
        return []

    read_write = []
    read_only = []
    no_access = []

    for path, level in access.items():
        if level == "read-write":
            read_write.append(path)
        elif level == "read-only":
            read_only.append(path)
        elif level == "no-access":
            no_access.append(path)

    norms = []
    if read_write:
        norms.append({"type": "scope", "role": role, "paths": read_write})
    if read_only:
        norms.append({"type": "readonly", "role": role, "paths": read_only})
    if no_access:
        norms.append({"type": "protected", "role": role, "paths": no_access})

    return norms


def _to_prolog_list(items: list[str]) -> str:
    """Convert a Python list to a Prolog list literal."""
    return "[" + ", ".join(str(item) for item in items) + "]"
