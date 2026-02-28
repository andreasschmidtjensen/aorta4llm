"""YAML -> Prolog fact compiler for organizational specifications."""

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
    _compile_norms(spec_dict.get("norms", []), spec)
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
    """Compile conditional norms to cond/5 facts."""
    for norm in norms:
        role = norm["role"]
        deon = norm["type"]  # 'obliged' or 'forbidden'
        objective = norm["objective"]
        deadline = norm.get("deadline", "false")
        condition = norm.get("condition", "true")
        spec.facts.append(f"cond({role}, {deon}, {objective}, {deadline}, {condition})")


def _compile_rules(rules: list, spec: CompiledSpec) -> None:
    """Pass through Prolog rules verbatim."""
    for rule in rules:
        rule = rule.strip()
        if not rule.endswith("."):
            rule += "."
        spec.rules.append(rule)


def _to_prolog_list(items: list[str]) -> str:
    """Convert a Python list to a Prolog list literal."""
    return "[" + ", ".join(str(item) for item in items) + "]"
